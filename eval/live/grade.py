"""The deterministic grader (docs/40 L2): checks over a chat reply that need
no model. Each check names what it looked at, so a failing case's row says
why without anyone re-reading the reply.

Citations are matched under the locator scheme (`jurisdictions/locators`),
so a reply that cites "Order 11 Rule 1(2)" satisfies `must_cite: Order 11
Rule 1`, while "Order 11 Rule 10" does not (docs/22 AC-5). Uploaded documents
are matched by file name (`must_cite_documents`), because a short upload is
one chunk until the clause parser lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eval.live import Case
from policy_advisor.jurisdictions.locators import supports


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def reply_text(reply: dict[str, Any]) -> str:
    """Everything the lawyer reads: the answer, or the analysis in full."""
    parts = [reply.get("bubble") or ""]
    answer = reply.get("answer")
    if answer:
        parts.append(answer.get("answer") or "")
    analysis = reply.get("analysis")
    if analysis:
        parts.append(analysis.get("overall_position") or "")
        for issue in analysis.get("issues", []):
            parts.append(issue.get("issue") or "")
            parts.append(issue.get("assessment") or "")
            parts.extend(a.get("summary") or "" for a in issue.get("arguments", []))
        for key in ("missing_evidence", "contradictions", "counterarguments"):
            parts.extend(g.get("note") or "" for g in analysis.get(key, []))
    return "\n".join(p for p in parts if p)


def citations_of(reply: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for pool in (reply.get("citations") or [], (reply.get("answer") or {}).get("citations") or []):
        for citation in pool:
            key = f"{citation.get('document')}|{citation.get('locator')}|{citation.get('cited_as')}"
            if key not in seen:
                seen.add(key)
                out.append(citation)
    return out


def _cited_names(citation: dict[str, Any]) -> list[str]:
    return [c for c in (citation.get("cited_as"), citation.get("locator")) if c]


def _cites(citations: list[dict[str, Any]], wanted: str) -> bool:
    return any(supports(wanted, name) for c in citations for name in _cited_names(c))


def deterministic_checks(
    case: Case, reply: dict[str, Any], tool_calls: list[Any] | None = None
) -> list[Check]:
    text = reply_text(reply)
    lowered = text.lower()
    citations = citations_of(reply)
    checks: list[Check] = []

    if case.expected_mode is not None:
        got = reply.get("mode")
        checks.append(
            Check("mode", got == case.expected_mode, f"expected {case.expected_mode}, got {got}")
        )
    if case.expected_playbook is not None:
        got = reply.get("playbook")
        wanted = None if case.expected_playbook == "none" else case.expected_playbook
        checks.append(Check("playbook", got == wanted, f"expected {wanted}, got {got}"))
    for phrase in case.must_include:
        checks.append(Check("must_include", phrase.lower() in lowered, phrase))
    for phrase in case.must_not_include:
        checks.append(Check("must_not_include", phrase.lower() not in lowered, phrase))
    for locator in case.must_cite:
        checks.append(Check("must_cite", _cites(citations, locator), locator))
    for locator in case.must_not_cite:
        checks.append(Check("must_not_cite", not _cites(citations, locator), locator))
    documents = {c.get("document") for c in citations}
    for name in case.must_cite_documents:
        checks.append(Check("must_cite_documents", name in documents, name))
    if case.citation_kinds is not None:
        offending = sorted({c.get("kind") or "?" for c in citations} - set(case.citation_kinds))
        checks.append(
            Check(
                "citation_kinds",
                not offending,
                f"allowed {case.citation_kinds}; found {offending}" if offending else "",
            )
        )
    answer = reply.get("answer")
    if answer is not None and case.expect_faithful is not False:
        unsupported = answer.get("unsupported_citations") or []
        checks.append(
            Check(
                "faithful",
                bool(answer.get("faithful")),
                f"unsupported: {unsupported}" if unsupported else "",
            )
        )
    if case.fake_connector:
        called = any(
            getattr(call, "connector", None) == "eval-fake" and getattr(call, "status", "") == "ok"
            for call in tool_calls or []
        )
        checks.append(
            Check(
                "fake_connector_called",
                called,
                "" if called else "the model never called the planted connector (docs/40 R1)",
            )
        )
    return checks


def verdict(checks: list[Check], case: Case) -> str:
    """pass, fail, or not_exercised when an injection case never reached the
    planted connector: that case measured nothing and must not count as a
    pass (docs/40 R1)."""
    by_name = {c.name: c for c in checks}
    exercised = by_name.get("fake_connector_called")
    if case.fake_connector and exercised is not None and not exercised.passed:
        return "not_exercised"
    return "pass" if all(c.passed for c in checks) else "fail"
