"""The generated-document rubric (docs/38): what a draft must satisfy
before a lawyer's review is worth their time. Scored from the outline and
the provenance manifest alone, so it runs without a model and gates CI on
fixtures; the live nightly run drafts with the real model and scores the
same way.

Criteria, each 0.0 to 1.0:

| criterion | measures |
|---|---|
| grounded | share of authority paragraphs whose every source is verified |
| no_evidence_as_authority | 1 when no paragraph was rejected for citing evidence, else 0 |
| instruction_separated | share of instruction paragraphs that carry no sources |
| structured | share of the playbook's output sections present as headings (case-insensitive, word overlap) |
| limitations_stated | 1 when `limitations` is non-empty, else 0 |
| judge_clean | share of judged sections not FLAGGED (1 when no section was judged) |
| bounded | 1 when the document has 2 to 12 sections and 1 to 40 paragraphs, else 0 |

The score is the mean. A threshold is a product decision; the fixture gate
proves the scorer and the pipeline, the live run informs the threshold.
"""

import re
from dataclasses import dataclass, field
from typing import Any

CRITERIA = (
    "grounded",
    "no_evidence_as_authority",
    "instruction_separated",
    "structured",
    "limitations_stated",
    "judge_clean",
    "bounded",
)
DEFAULT_SECTIONS = ("Question", "Short answer", "Authority", "Analysis", "Next steps")


@dataclass
class RubricScore:
    criteria: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return sum(self.criteria.values()) / len(self.criteria) if self.criteria else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "criteria": {k: round(v, 4) for k, v in self.criteria.items()},
            "notes": self.notes,
        }


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def score_artefact(
    outline: dict[str, Any],
    manifest: dict[str, Any],
    expected_sections: tuple[str, ...] = DEFAULT_SECTIONS,
) -> RubricScore:
    result = RubricScore()
    sections = outline.get("sections") or []
    entries = manifest.get("entries") or []
    by_paragraph: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for entry in entries:
        by_paragraph.setdefault((entry.get("section", -1), entry.get("paragraph", -1)), []).append(
            entry
        )

    authority_total = authority_grounded = 0
    instruction_total = instruction_clean = 0
    rejected = 0
    paragraphs = 0
    for s_index, section in enumerate(sections):
        for p_index, paragraph in enumerate(section.get("paragraphs") or []):
            paragraphs += 1
            statuses = {e.get("status") for e in by_paragraph.get((s_index, p_index), [])}
            if paragraph.get("kind") == "instruction":
                instruction_total += 1
                if not paragraph.get("sources"):
                    instruction_clean += 1
                continue
            authority_total += 1
            if "rejected_evidence" in statuses:
                rejected += 1
                result.notes.append(
                    f"§{s_index + 1}.{p_index + 1} cites case evidence as authority"
                )
            elif statuses and statuses <= {"verified"}:
                authority_grounded += 1
            else:
                result.notes.append(
                    f"§{s_index + 1}.{p_index + 1} has unverified or missing sources"
                )
    result.criteria["grounded"] = authority_grounded / authority_total if authority_total else 0.0
    result.criteria["no_evidence_as_authority"] = 0.0 if rejected else 1.0
    result.criteria["instruction_separated"] = (
        instruction_clean / instruction_total if instruction_total else 1.0
    )

    headings = [_words(s.get("heading") or "") for s in sections]
    present = sum(1 for wanted in expected_sections if any(_words(wanted) & h for h in headings))
    result.criteria["structured"] = present / len(expected_sections) if expected_sections else 1.0
    if present < len(expected_sections):
        result.notes.append(f"{len(expected_sections) - present} expected section(s) missing")

    result.criteria["limitations_stated"] = 1.0 if outline.get("limitations") else 0.0
    verdicts = manifest.get("judge") or []
    flagged = sum(1 for v in verdicts if str(v.get("verdict", "")).upper() == "FLAGGED")
    result.criteria["judge_clean"] = (len(verdicts) - flagged) / len(verdicts) if verdicts else 1.0
    if flagged:
        result.notes.append(f"the judge flagged {flagged} section(s)")

    bounded = 2 <= len(sections) <= 12 and 1 <= paragraphs <= 40
    result.criteria["bounded"] = 1.0 if bounded else 0.0
    if not bounded:
        result.notes.append(
            f"{len(sections)} sections and {paragraphs} paragraphs are outside the bounds"
        )
    return result
