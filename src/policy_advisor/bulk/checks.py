"""Cross-document checks over a matter's extractions (docs/33).

Deterministic where the facts are strings a program can compare: the same
party defined differently, the same key date written differently, the same
defined term with two definitions, governing law that differs across the
set. Model-based where judgment is needed: obligations that pull against
each other across documents, asked of the escalation model over a compact
list and under the job's ceiling. Every finding names its documents and
carries the evidence locators, and starts `open` for the review queue.
"""

import re
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

_NOISE = re.compile(r"\b(limited|ltd\.?|llc|inc\.?|plc|the|of|and)\b|[^a-z0-9 ]")


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", _NOISE.sub(" ", name.lower())).strip()


def _locators(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for e in item.get("evidence", []):
        if isinstance(e, dict) and e.get("locator"):
            out.append(str(e["locator"]))
    return out


def deterministic_findings(extractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    # Parties: one normalised name, several defined terms or roles.
    parties: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in extractions:
        for party in row["record"].get("parties", []):
            key = _key(party.get("name", ""))
            if not key:
                continue
            label = f"{party.get('defined_as') or ''}|{party.get('role') or ''}"
            parties[key][label].add(row["source_document"])
            evidence[key].append(
                {
                    "document": row["source_document"],
                    "locators": _locators(party),
                    "defined_as": party.get("defined_as"),
                    "role": party.get("role"),
                }
            )
    for key, labels in parties.items():
        if len(labels) > 1:
            documents = sorted({d for docs in labels.values() for d in docs})
            findings.append(
                {
                    "kind": "party_mismatch",
                    "severity": "review",
                    "documents": documents,
                    "summary": f"'{key}' appears under different defined terms or roles across {len(documents)} documents: "
                    + "; ".join(sorted(label.replace("|", " / ") for label in labels)),
                    "evidence": evidence[key],
                }
            )

    # Key dates: the same label, different text.
    dates: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    date_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in extractions:
        for item in row["record"].get("key_dates", []):
            label = _key(item.get("label", ""))
            if label:
                dates[label][str(item.get("date_text", "")).strip()].add(row["source_document"])
                date_evidence[label].append(
                    {
                        "document": row["source_document"],
                        "locators": _locators(item),
                        "date_text": item.get("date_text"),
                    }
                )
    for label, texts in dates.items():
        if len(texts) > 1:
            documents = sorted({d for docs in texts.values() for d in docs})
            findings.append(
                {
                    "kind": "date_conflict",
                    "severity": "high",
                    "documents": documents,
                    "summary": f"'{label}' is written differently across {len(documents)} documents: "
                    + "; ".join(sorted(texts)),
                    "evidence": date_evidence[label],
                }
            )

    # Defined terms: same term, different definitions.
    terms: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    term_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in extractions:
        for item in row["record"].get("defined_terms", []):
            term = _key(item.get("term", ""))
            if term:
                terms[term][
                    re.sub(r"\s+", " ", str(item.get("definition", ""))).strip().lower()
                ].add(row["source_document"])
                term_evidence[term].append(
                    {"document": row["source_document"], "locators": _locators(item)}
                )
    for term, definitions in terms.items():
        if len(definitions) > 1:
            documents = sorted({d for docs in definitions.values() for d in docs})
            findings.append(
                {
                    "kind": "defined_term_conflict",
                    "severity": "review",
                    "documents": documents,
                    "summary": f"'{term}' is defined {len(definitions)} different ways across {len(documents)} documents",
                    "evidence": term_evidence[term],
                }
            )

    # Governing law across the set.
    laws: dict[str, set[str]] = defaultdict(set)
    for row in extractions:
        law = _key(str(row["record"].get("governing_law", "")))
        if law:
            laws[law].add(row["source_document"])
    if len(laws) > 1:
        findings.append(
            {
                "kind": "governing_law_varies",
                "severity": "info",
                "documents": sorted({d for docs in laws.values() for d in docs}),
                "summary": "Governing law differs across the set: "
                + "; ".join(f"{law} ({len(docs)})" for law, docs in sorted(laws.items())),
                "evidence": [],
            }
        )
    return findings


class ObligationConflict(BaseModel):
    documents: list[str] = Field(description="The documents whose obligations conflict")
    summary: str = Field(
        description="What pulls against what, in one or two sentences, quoting the obligations' own words"
    )
    locators: list[str] = Field(
        default_factory=list, description="The clause locators involved, exactly as given"
    )


class ObligationConflicts(BaseModel):
    conflicts: list[ObligationConflict] = Field(default_factory=list)


OBLIGATION_CHECK_INSTRUCTIONS = """You are given obligations extracted from several documents of one matter, each with its document and clause locator. Identify only genuine conflicts: an obligation in one document that cannot be performed without breaching another, the same obligor owing inconsistent deadlines or standards for the same performance, or an exclusivity, non-compete or assignment restriction one document imposes that another document's obligation would breach. Do not list mere differences, and do not assess fairness. Quote the obligations' own words and copy the locators exactly. Return no conflicts when there are none."""


def compact_obligations(
    extractions: list[dict[str, Any]], max_items: int = 200
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in extractions:
        for item in row["record"].get("obligations", []):
            items.append(
                {
                    "document": row["source_document"],
                    "obligor": item.get("obligor"),
                    "obligee": item.get("obligee"),
                    "obligation": item.get("obligation"),
                    "deadline": item.get("deadline"),
                    "locators": _locators(item),
                }
            )
            if len(items) >= max_items:
                return items
    return items


def model_conflict_findings(conflicts: ObligationConflicts) -> list[dict[str, Any]]:
    return [
        {
            "kind": "obligation_conflict",
            "severity": "high",
            "documents": sorted(set(c.documents)),
            "summary": c.summary,
            "evidence": [{"locators": c.locators}],
        }
        for c in conflicts.conflicts
        if c.documents
    ]
