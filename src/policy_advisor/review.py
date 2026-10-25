"""Review states for artefacts and findings (docs/30 D7; docs/36).

Artefacts: draft -> reviewed -> approved, reviewed or draft -> rejected,
rejected -> draft. An approved artefact is immutable. Findings: open ->
reviewed, open or reviewed -> dismissed, dismissed -> open.

Who may do what (docs/31 roles):
- `review` (draft->reviewed, open->reviewed): anyone with access to the
  matter, reviewers included; it means "I have read this".
- `approve` and `reject` (artefacts), `dismiss` (findings): the reviewer
  or admin role. A lawyer who owns the matter does not approve their own
  draft; the point of the state is a second pair of eyes.
- `reopen` (rejected->draft, dismissed->open): a writer on the matter
  (owner or writable share), never a reviewer.

Every transition writes a `review_events` row with who, which role, from
and to, and the note. Refusals are typed so the API maps them to 403, 409
or 404 without string matching.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from policy_advisor.api.credentials import UserRecord
from policy_advisor.api.deps import MatterAccess
from policy_advisor.artefacts.store import ArtefactRecord, get_artefact, set_artefact_status
from policy_advisor.bulk.jobs import get_finding, list_findings, set_finding_status
from policy_advisor.db import connect

REVIEWER_ROLES = frozenset({"reviewer", "admin"})

ARTEFACT_TRANSITIONS: dict[str, dict[str, str]] = {
    "review": {"draft": "reviewed"},
    "approve": {"reviewed": "approved"},
    "reject": {"reviewed": "rejected", "draft": "rejected"},
    "reopen": {"rejected": "draft"},
}
FINDING_TRANSITIONS: dict[str, dict[str, str]] = {
    "review": {"open": "reviewed"},
    "dismiss": {"open": "dismissed", "reviewed": "dismissed"},
    "reopen": {"dismissed": "open"},
}
ACTIONS = ("review", "approve", "reject", "dismiss", "reopen")


class ReviewRefused(Exception):
    """Base: the transition is not allowed; `code` names why."""

    code = "review_refused"


class NotPermitted(ReviewRefused):
    code = "role_forbidden"


class WrongState(ReviewRefused):
    code = "wrong_state"


class Immutable(ReviewRefused):
    code = "immutable"


@dataclass(frozen=True)
class ReviewEvent:
    id: int
    subject_kind: str
    subject_id: str
    action: str
    from_status: str
    to_status: str
    username: str | None
    role: str | None
    note: str | None
    created_at: datetime | None


def _check_role(action: str, user: UserRecord, access: MatterAccess) -> None:
    if action == "review":
        return
    if action in ("approve", "reject", "dismiss"):
        if user.role not in REVIEWER_ROLES:
            raise NotPermitted(f"only a reviewer or admin may {action}; your role is {user.role}")
        return
    if action == "reopen":
        if access.read_only:
            raise NotPermitted("only a writer on this matter may reopen")
        return
    raise NotPermitted(f"unknown action {action!r}")


def _next_status(table: dict[str, dict[str, str]], action: str, current: str, subject: str) -> str:
    if subject == "artefact" and current == "approved":
        raise Immutable("an approved artefact cannot change")
    transitions = table.get(action)
    if transitions is None:
        raise WrongState(f"{action!r} does not apply to a {subject}")
    if current not in transitions:
        raise WrongState(f"a {subject} that is {current} cannot be {action}ed")
    return transitions[current]


def _record_event(
    *,
    matter_id: str,
    subject_kind: str,
    subject_id: str,
    action: str,
    from_status: str,
    to_status: str,
    user: UserRecord,
    note: str | None,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO review_events (matter_id, subject_kind, subject_id, action, from_status, to_status, username, role, note) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                matter_id,
                subject_kind,
                subject_id,
                action,
                from_status,
                to_status,
                user.username,
                user.role,
                (note or "")[:2000] or None,
            ),
        )


def transition_artefact(
    artefact_id: str,
    action: str,
    *,
    user: UserRecord,
    access: MatterAccess,
    note: str | None = None,
) -> ArtefactRecord:
    record = get_artefact(artefact_id)
    if record is None or record.matter_id != access.id:
        raise KeyError(artefact_id)
    _check_role(action, user, access)
    to_status = _next_status(ARTEFACT_TRANSITIONS, action, record.status, "artefact")
    updated = set_artefact_status(artefact_id, to_status)
    _record_event(
        matter_id=access.id,
        subject_kind="artefact",
        subject_id=artefact_id,
        action=action,
        from_status=record.status,
        to_status=to_status,
        user=user,
        note=note,
    )
    return updated


def transition_finding(
    finding_id: int, action: str, *, user: UserRecord, access: MatterAccess, note: str | None = None
) -> dict[str, Any]:
    finding = get_finding(finding_id)
    if finding is None or finding["matter_id"] != access.id:
        raise KeyError(str(finding_id))
    _check_role(action, user, access)
    to_status = _next_status(FINDING_TRANSITIONS, action, finding["status"], "finding")
    updated = set_finding_status(finding_id, to_status)
    _record_event(
        matter_id=access.id,
        subject_kind="finding",
        subject_id=str(finding_id),
        action=action,
        from_status=finding["status"],
        to_status=to_status,
        user=user,
        note=note,
    )
    return updated


def events_for(subject_kind: str, subject_id: str) -> list[ReviewEvent]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, subject_kind, subject_id, action, from_status, to_status, username, role, note, created_at "
            "FROM review_events WHERE subject_kind = %s AND subject_id = %s ORDER BY created_at, id",
            (subject_kind, subject_id),
        ).fetchall()
    return [ReviewEvent(**dict(r)) for r in rows]


def review_queue(matter_id: str) -> dict[str, list[Any]]:
    """What still needs a decision on a matter: artefacts not yet approved
    or rejected, findings not yet dismissed; oldest first."""
    from policy_advisor.artefacts.store import list_artefacts

    artefacts = [a for a in list_artefacts(matter_id) if a.status in ("draft", "reviewed")]
    artefacts.sort(key=lambda a: a.created_at or datetime.min.replace(tzinfo=None))
    findings = [f for f in list_findings(matter_id) if f["status"] in ("open", "reviewed")]
    return {"artefacts": artefacts, "findings": findings}
