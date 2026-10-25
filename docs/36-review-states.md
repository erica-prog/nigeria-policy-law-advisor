# 36 — Review states and the review queue

**Status:** implemented (docs/30 slice P3-F). **Decisions:** docs/30 D7 (states and
transitions) and D2 (roles).

## Why a state machine

Bulk analysis (docs/33) and drafting (docs/35) produce things a lawyer has not yet read:
findings across documents, memos and decks. Until someone has read them they are
proposals, and the system must not let a proposal look like a decision. The state on
each row says where it stands; the events table says who moved it and why. Nothing the
system makes is sent, filed or final on its own.

## The states

| Subject | States | Transitions |
|---|---|---|
| Artefact | `draft` → `reviewed` → `approved`; `draft` or `reviewed` → `rejected`; `rejected` → `draft` | `review`, `approve`, `reject`, `reopen` |
| Finding | `open` → `reviewed`; `open` or `reviewed` → `dismissed`; `dismissed` → `open` | `review`, `dismiss`, `reopen` |

An approved artefact is immutable: every transition on it is refused with `409
immutable`. Nothing edits an approved file; a change is a new draft.

## Who may do what

| Action | Who |
|---|---|
| `review` ("I have read this") | anyone with access to the matter, reviewers included |
| `approve`, `reject`, `dismiss` | the `reviewer` or `admin` role; a lawyer does not approve their own draft, which is what the state is for |
| `reopen` | a writer on the matter (owner, or a share with write), never a reviewer |

The rules live in `src/policy_advisor/review.py` and are enforced there, not in the
client. Refusals are typed (`role_forbidden` 403, `wrong_state` 409, `immutable` 409) so
the API never string-matches. Each transition writes a `review_events` row (migration
0013): subject, action, from and to, user and role, note, time. Events are
tenant-scoped like everything else (docs/31).

## The API (contract: docs/contracts/web-api.md)

`POST /api/matters/{id}/artefacts/{artefact_id}/review` and `POST
/api/matters/{id}/bulk/findings/{finding_id}/review` take `{ "action", "note" }`;
`GET .../events` on either returns the trail; `GET /api/matters/{id}/review-queue`
returns the artefacts and findings that still await a decision. Reviewers reach all of
these on a matter shared with them read-only.

## The client

Two dialogs in the browser client (`web/js/review.js`):

- **The review queue**, opened from the cases drawer, lists the case's draft and reviewed
  artefacts and its open and reviewed findings, each with the actions this user's role
  can take. The client hides buttons the server would refuse; the server still decides.
  An action asks for an optional note, which goes into the trail.
- **The artefact viewer** shows the outline with instruction paragraphs in italics,
  removed paragraphs marked, unverified ones flagged, the judge's verdict per section,
  the provenance table entry by entry, the downloads, and the event trail.

Teaching note: the client is plain ES modules with no build step, as everywhere in
`web/`; the two factories take the `api` object and the dialog element and return
`open`, which is how the existing passage and activation dialogs work.

## Not done here

- Notifications or an inbox across matters: the queue is per case.
- Editing a draft's text in the client; a change is a new drafting run.
- A rubric that scores generated documents in CI (P3-H).
