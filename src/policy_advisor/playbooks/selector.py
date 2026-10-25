"""The route for a chat message: a mode and, where one fits, a playbook
(the generalisation of `decide_mode`, ADR-001; docs/29).

Order of decision:

1. The lawyer's explicit intent wins the mode (`analyze`, `ask`, `research`).
2. A playbook is chosen when the message, or the matter's jurisdiction,
   points at one: its country must match the jurisdiction when one is given,
   and its triggers must appear in the message. The best-scoring playbook
   wins; ties go to the one whose jurisdiction list names the matter's.
3. With `auto` intent, a matched playbook decides the mode: its own mode,
   except that analysis needs documents and falls back to research without
   them. With no playbook, the Phase 2 rules apply: analysis for the first
   message of an unanalysed conversation with documents, research without
   documents, otherwise a question.

The selector never calls a model; the routing evals run it as is.
"""

import re
from dataclasses import dataclass

from policy_advisor.jurisdictions import UnknownJurisdiction, load_taxonomy
from policy_advisor.playbooks import Playbook, load_playbooks


@dataclass(frozen=True)
class Selection:
    mode: str
    playbook: Playbook | None
    reason: str

    @property
    def playbook_id(self) -> str | None:
        return self.playbook.id if self.playbook else None


def _country_of(jurisdiction: str | None) -> str | None:
    if not jurisdiction:
        return None
    try:
        resolved = load_taxonomy().resolve(jurisdiction)
    except UnknownJurisdiction:
        return None
    return resolved.country if resolved else None


def _trigger_score(message: str, playbook: Playbook) -> int:
    text = message.lower()
    score = 0
    for trigger in playbook.triggers:
        if re.search(r"(?<![a-z])" + re.escape(trigger) + r"(?![a-z])", text):
            score += 2 if " " in trigger else 1  # a phrase is stronger evidence than a word
    return score


def match_playbook(
    message: str, jurisdiction: str | None, playbooks: dict[str, Playbook] | None = None
) -> tuple[Playbook | None, int]:
    """The best playbook for a message, with its trigger score (0 = none)."""
    country = _country_of(jurisdiction)
    canonical = None
    if jurisdiction:
        try:
            resolved = load_taxonomy().resolve(jurisdiction)
            canonical = resolved.id if resolved else None
        except UnknownJurisdiction:
            canonical = None
    best: Playbook | None = None
    best_key: tuple[int, int] = (0, 0)
    for playbook in (playbooks or load_playbooks()).values():
        if country is not None and playbook.country != country:
            continue
        score = _trigger_score(message, playbook)
        if score == 0:
            continue
        key = (score, 1 if canonical and canonical in playbook.jurisdictions else 0)
        if key > best_key:
            best, best_key = playbook, key
    return best, best_key[0]


def select_route(
    message: str,
    *,
    intent: str = "auto",
    has_documents: bool,
    read_only: bool,
    conversation_started: bool,
    analysed: bool,
    jurisdiction: str | None = None,
    playbooks: dict[str, Playbook] | None = None,
) -> Selection:
    playbook, score = match_playbook(message, jurisdiction, playbooks)
    why = (
        f"playbook {playbook.id} matched {score} trigger point(s)"
        if playbook
        else "no playbook matched"
    )

    if intent in ("analyze", "ask", "research"):
        mode = {"analyze": "analysis", "ask": "question", "research": "research"}[intent]
        if mode == "analysis" and not has_documents:
            mode = "research"
            why += "; analysis asked for without documents, so research"
        return Selection(mode=mode, playbook=playbook, reason=f"intent {intent}; {why}")

    if playbook is not None:
        mode = playbook.mode
        if mode == "analysis" and not has_documents:
            mode = "research"
            why += "; no documents, so research instead of analysis"
        if mode == "analysis" and read_only:
            mode = "question"
            why += "; the shared library is not a case, so a question"
        return Selection(mode=mode, playbook=playbook, reason=why)

    wants_analysis = not read_only and not analysed and not conversation_started
    if not wants_analysis:
        return Selection(
            mode="question", playbook=None, reason=f"{why}; follow-up or shared library"
        )
    if has_documents:
        return Selection(
            mode="analysis", playbook=None, reason=f"{why}; first message with documents"
        )
    return Selection(
        mode="research", playbook=None, reason=f"{why}; first message without documents"
    )
