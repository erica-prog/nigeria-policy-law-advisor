#!/usr/bin/env python3
"""Check: removing a document while it is still being read does not leave it in the case.

Models every interleaving of delete_document and JobTable.run, then drives
those two functions through the interleaving the model calls a violation.
Exit 1 when that violation is reachable. Exit 2 when the source no longer
matches a shape this file knows. Exit 0 only when no violating end state
is reachable.

    uv run python .cursor/skills/verify-document-cancel/check_document_cancel.py
"""

from __future__ import annotations

import inspect
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JOBS = REPO / "src" / "policy_advisor" / "api" / "jobs.py"
DOCUMENTS = REPO / "src" / "policy_advisor" / "api" / "routers" / "documents.py"

# Sync commit when this check was written. jobs.py and documents.py were
# uncommitted, so HEAD does not contain them. Re-read both files before
# moving SYNC_COMMIT forward.
SYNC_COMMIT = "9bc78af2248a06816771233a9c4276bb5d8bb5e8"


def _shape(run_source: str) -> str:
    has_check = "if job.cancelled:" in run_source
    has_ready = 'job.status = "ready"' in run_source
    if not has_ready:
        return "unknown"
    if not has_check:
        return "no-check"
    check_at = run_source.index("if job.cancelled:")
    ready_at = run_source.index('job.status = "ready"')
    ingest_at = run_source.index("with services.ingest_lock:")
    if not (ingest_at < check_at < ready_at):
        return "unknown"
    # The check is inside the ingest lock only when it is indented deeper
    # than the with-line. The same indent means it runs after that lock.
    ingest_line = next(
        line for line in run_source.splitlines() if "with services.ingest_lock:" in line
    )
    check_line = next(line for line in run_source.splitlines() if "if job.cancelled:" in line)
    if len(check_line) - len(check_line.lstrip()) > len(ingest_line) - len(ingest_line.lstrip()):
        return "unknown"
    return "check-after-ingest"


def _search(shape: str) -> list[str] | None:
    """Return one shortest violating trace, or None if every finished state is safe.

    Steps are the lock-sized actions in the current functions. delete_document's
    list_documents test and forget_document are separate. JobTable.run's
    add_document call is one step; the cancelled read and the ready write are
    two more, because nothing holds them together.
    """
    # delete: 0 not started, 1 saw whether chunks existed, 2 acted on that, 3 forgotten
    # run: 0 not started, 1 ingested, 2 read cancelled, 3 finished
    start = {
        "chunks": False,
        "cancelled": False,
        "status": "queued",
        "seen": None,
        "saw_chunks": None,
        "delete": 0,
        "run": 0,
    }
    queue = [(start, [])]
    seen = set()
    while queue:
        state, trace = queue.pop(0)
        key = tuple(sorted(state.items()))
        if key in seen:
            continue
        seen.add(key)
        if state["delete"] == 3 and state["run"] == 3:
            if state["chunks"]:
                return trace
            continue
        for label, nxt in _steps(state, shape):
            queue.append((nxt, trace + [label]))
    return None


def _steps(state: dict, shape: str) -> list[tuple[str, dict]]:
    out = []
    if state["delete"] == 0:
        nxt = dict(state)
        nxt["saw_chunks"] = state["chunks"]
        nxt["delete"] = 1
        word = "present" if state["chunks"] else "absent"
        out.append((f"delete_document reads the case and the chunks are {word}", nxt))
    elif state["delete"] == 1:
        nxt = dict(state)
        nxt["delete"] = 2
        if state["saw_chunks"]:
            nxt["chunks"] = False
            out.append(("delete_document removes the saved chunks", nxt))
        else:
            out.append(("delete_document does not remove chunks, because it saw none", nxt))
    elif state["delete"] == 2:
        nxt = dict(state)
        nxt["cancelled"] = True
        nxt["delete"] = 3
        out.append(("forget_document sets cancelled and drops the job", nxt))
    if state["run"] == 0:
        nxt = dict(state)
        nxt["chunks"] = True
        nxt["status"] = "processing"
        nxt["run"] = 1
        out.append(("JobTable.run finishes add_document and the chunks are saved", nxt))
    elif state["run"] == 1:
        nxt = dict(state)
        nxt["run"] = 2
        if shape == "no-check":
            nxt["seen"] = False
            out.append(("JobTable.run has no cancelled check and keeps the document", nxt))
        else:
            nxt["seen"] = state["cancelled"]
            word = "true" if state["cancelled"] else "false"
            out.append((f"JobTable.run reads job.cancelled and sees {word}", nxt))
    elif state["run"] == 2:
        nxt = dict(state)
        nxt["run"] = 3
        if state["seen"]:
            nxt["chunks"] = False
            nxt["status"] = "processing"
            out.append(("JobTable.run removes the chunks and returns", nxt))
        else:
            nxt["status"] = "ready"
            out.append(('JobTable.run sets status to "ready" without reading cancelled again', nxt))
    return out


def _confirm_on_real_code(shape: str) -> dict:
    """Drive the real functions through the violating schedule. Returns what happened."""
    from policy_advisor.api.deps import MatterAccess
    from policy_advisor.api.jobs import JobTable
    from policy_advisor.api.routers.documents import delete_document
    import policy_advisor.api.routers.documents as documents
    import policy_advisor.ingestion.ingest_document as ingest
    from policy_advisor.logging_utils import get_logger

    chunks: set[str] = set()
    removes: list[str] = []
    list_calls = {"n": 0}
    second_list = threading.Event()
    decided_keep = threading.Event()
    delete_finished = threading.Event()
    name = "supply.docx"
    matter_id = "case-race"

    def list_documents(_matter_id: str) -> list[str]:
        list_calls["n"] += 1
        snapshot = sorted(chunks)
        if list_calls["n"] == 2:
            second_list.set()
        return snapshot

    def add_document(*_args, **_kwargs) -> int:
        if not second_list.wait(5):
            raise TimeoutError("delete_document never read the case twice")
        chunks.add(name)
        return 1

    def remove_document(_matter_id: str, source: str) -> None:
        removes.append(source)
        chunks.discard(source)

    documents.list_documents = list_documents
    documents.remove_document = remove_document
    ingest.add_document = add_document
    ingest.remove_document = remove_document

    class Services:
        def __init__(self) -> None:
            self.ingest_lock = threading.Lock()
            self.logger = get_logger("verify-document-cancel")

        def invalidate_matter(self, _matter_id: str) -> None:
            return None

    table = JobTable()
    job = table.create(matter_id, name, Path("."))
    job.temp_dir = None
    services = Services()
    matter = MatterAccess(id=matter_id, owner="verify", read_only=False)
    run_lines, run_start = inspect.getsourcelines(type(table).run)
    ready_line = next(
        run_start + index
        for index, line in enumerate(run_lines)
        if 'job.status = "ready"' in line
    )
    delete_lines, delete_start = inspect.getsourcelines(delete_document)
    forget_line = next(
        delete_start + index
        for index, line in enumerate(delete_lines)
        if "forget_document" in line
    )

    def trace_run(frame, event, _arg):
        if event == "line" and frame.f_code.co_name == "run" and frame.f_lineno == ready_line:
            decided_keep.set()
            if not delete_finished.wait(5):
                raise TimeoutError("delete_document did not finish")
        return trace_run

    def trace_delete(frame, event, _arg):
        if (
            event == "line"
            and frame.f_code.co_name == "delete_document"
            and frame.f_lineno == forget_line
        ):
            if not decided_keep.wait(5):
                raise TimeoutError("JobTable.run never decided to keep the document")
        return trace_delete

    def worker() -> None:
        sys.settrace(trace_run)
        try:
            table.run(job, services, None, None)
        finally:
            sys.settrace(None)

    thread = threading.Thread(target=worker)
    thread.start()
    sys.settrace(trace_delete)
    try:
        delete_document(name, matter, table, services)
    finally:
        sys.settrace(None)
    delete_finished.set()
    thread.join(5)
    if thread.is_alive():
        raise TimeoutError("JobTable.run did not finish")
    return {
        "shape": shape,
        "chunks": sorted(chunks),
        "removes": removes,
        "status": job.status,
        "cancelled": job.cancelled,
    }


def _control_forget_during_ingest() -> bool:
    """Forget before the cancelled read. The current check removes the chunks."""
    from policy_advisor.api.jobs import JobTable
    import policy_advisor.ingestion.ingest_document as ingest
    from policy_advisor.logging_utils import get_logger

    chunks: set[str] = set()
    name = "supply.docx"

    class Services:
        def __init__(self) -> None:
            self.ingest_lock = threading.Lock()
            self.logger = get_logger("verify-document-cancel")

        def invalidate_matter(self, _matter_id: str) -> None:
            return None

    table = JobTable()
    job = table.create("case-control", name, Path("."))
    job.temp_dir = None

    def add_document(*_args, **_kwargs) -> int:
        chunks.add(name)
        table.forget_document("case-control", name)
        return 1

    def remove_document(_matter_id: str, source: str) -> None:
        chunks.discard(source)

    ingest.add_document = add_document
    ingest.remove_document = remove_document
    table.run(job, Services(), None, None)
    return name not in chunks and job.status != "ready"


def main() -> None:
    from policy_advisor.api.jobs import JobTable
    from policy_advisor.api.routers.documents import delete_document

    run_source = inspect.getsource(JobTable.run)
    forget_source = inspect.getsource(JobTable.forget_document)
    delete_source = inspect.getsource(delete_document)
    if "with self._lock:" not in forget_source or "forget_document" not in delete_source:
        print("model drift: forget_document or delete_document no longer matches the model")
        raise SystemExit(2)
    shape = _shape(run_source)
    if shape == "unknown":
        print("model drift: JobTable.run is no longer check-after-ingest or no-check")
        print(f"covered files: {JOBS}")
        print(f"covered files: {DOCUMENTS}")
        print(f"sync commit: {SYNC_COMMIT} (working tree; these files were uncommitted)")
        raise SystemExit(2)
    trace = _search(shape)
    print(f"shape: {shape}")
    print(f"sync commit: {SYNC_COMMIT}")
    if trace is None:
        print("property holds: no finished state still has the chunks")
        raise SystemExit(0)
    print("property fails: the user removed the document and it is still in the case")
    for index, step in enumerate(trace, start=1):
        print(f"{index}. {step}")
    if shape == "check-after-ingest":
        observed = _confirm_on_real_code(shape)
        print(
            "real functions:"
            f" status={observed['status']}"
            f" cancelled={observed['cancelled']}"
            f" chunks={observed['chunks']}"
            f" removes={observed['removes']}"
        )
        if observed["chunks"] != ["supply.docx"] or observed["status"] != "ready":
            print("model drift: the real schedule did not leave the document ready")
            raise SystemExit(2)
        control = _control_forget_during_ingest()
        print(f"control forget-during-ingest removes the document: {control}")
        if not control:
            print("the cancelled check did not remove chunks when forget ran during add_document")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
