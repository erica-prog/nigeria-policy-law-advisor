---
name: verify-document-cancel
description: >-
  Check that removing a Law & Policy Advisor document while ingestion is still
  reading it does not leave that document in the case. Use when a change
  touches JobTable.run, JobTable.forget_document, or delete_document, or when
  an upload might remain after the user removes it.
---

# Document removal during ingestion

A person can remove a document while the server is still reading it. The case must not keep that document after both the removal and the read have finished.

The check enumerates the interleavings of `delete_document` and `JobTable.run`, then drives those two functions through the violating schedule. It uses Python, which this repo already runs with `uv`. The steps are few, and the confirmation has to call the real functions. A separate TLA+ install is not required to rerun it.

## When a change touches this

Run the check when a diff touches any of:

- `JobTable.run` and `JobTable.forget_document` in `src/policy_advisor/api/jobs.py`
- `delete_document` in `src/policy_advisor/api/routers/documents.py`

The covered behavior is the block in `run` that calls `add_document` under `services.ingest_lock`, then reads `job.cancelled`, then either calls `remove_document` or sets `status` to `ready`. `forget_document` sets `cancelled` under `self._lock`. `delete_document` decides whether to call `remove_document` from `list_documents` before it calls `forget_document`. Those are separate steps. The ingest lock and the job-table lock are not the same lock.

Last confirmed against the working tree at commit `9bc78af2248a06816771233a9c4276bb5d8bb5e8`. That commit does not contain these two files; they were uncommitted when the check was written. Move this commit forward only after you have read the functions at the new commit and the check still describes them. If the check prints `model drift`, the source changed shape. Update this skill before treating the result as a pass or a product bug.

## Run

From the repository root:

```bash
uv run python .claude/skills/verify-document-cancel/check_document_cancel.py
```

The run takes a few seconds. It does not start the web server and it does not read `data/`.

Exit codes:

- `0` means no finished interleaving still has the chunks. The current code does not do this.
- `1` means a finished interleaving still has the chunks. The command prints the steps. That is a product finding, not a modeling error, while the printed shape is `check-after-ingest` and the `real functions` line shows `status=ready`, `cancelled=True`, `chunks=['supply.docx']`, and `removes=[]`.
- `2` means the source no longer matches a shape this file models, or the real schedule disagreed with the model. Read the functions before changing the property.

A second line, `control forget-during-ingest removes the document: True`, is printed only for shape `check-after-ingest`. It is not the property. It checks the narrower schedule where `forget_document` runs inside `add_document`, before `run` reads `cancelled`. Deleting the `if job.cancelled` branch changes the shape to `no-check` and the trace says `JobTable.run has no cancelled check`. Restore the branch. Do not weaken the property to make exit 1 go away.

## What a failure means

While the shape is `check-after-ingest`, exit `1` with the steps below is the known finding. Report it in those words. Do not change `jobs.py` or `documents.py` unless the person asked for a fix.

1. `delete_document` reads the case and the chunks are absent.
2. `delete_document` does not remove chunks, because it saw none.
3. `JobTable.run` finishes `add_document` and the chunks are saved.
4. `JobTable.run` reads `job.cancelled` and sees false.
5. `forget_document` sets cancelled and drops the job.
6. `JobTable.run` sets status to `ready` without reading cancelled again.

Harm: the person removed the document, and the saved chunks are still in the case.

## What this does not cover

It does not cover chat jobs, session cookies, or a delete that happens after `status` is already `ready` and `list_documents` can see the chunks. That later delete calls `remove_document` itself. It does not prove the embedding index matches `chunks.json`. The model treats `add_document` and `forget_document` as single steps because each holds its own lock for the whole call.
