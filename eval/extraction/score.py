"""Scoring an extraction against its label (docs/34).

Eight fields, each scored as a set of items with precision, recall and F1,
then micro-averaged across the documents of a run:

| field | an item is |
|---|---|
| parties | (normalised name, normalised role) |
| governing_law | the normalised law text (one item per document) |
| key_dates | (normalised label, normalised date text) |
| obligations | (normalised obligor, normalised deadline, obligation words), matched when obligor and deadline agree and the obligation text shares at least half its words |
| obligation_evidence | of the obligations matched, the labelled locator is among the predicted evidence locators |
| defined_terms | the normalised term |
| risk_clauses | the normalised kind |
| classification | the normalised classification (one item) |

Normalisation lowercases, strips punctuation and articles, and collapses
whitespace, so "the Buyer" and "Buyer" agree and "Ltd." and "Limited" do not
count against a model that copied the document faithfully.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

FIELDS = (
    "parties",
    "governing_law",
    "key_dates",
    "obligations",
    "obligation_evidence",
    "defined_terms",
    "risk_clauses",
    "classification",
)

_STRIP = re.compile(r"\b(the|a|an|of|ltd\.?|limited|llc|inc\.?|plc)\b|[^a-z0-9 ]")


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", _STRIP.sub(" ", str(value or "").lower())).strip()


def _words(value: Any) -> set[str]:
    return set(norm(value).split())


@dataclass
class FieldScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def add_sets(self, predicted: set[Any], labelled: set[Any]) -> None:
        self.tp += len(predicted & labelled)
        self.fp += len(predicted - labelled)
        self.fn += len(labelled - predicted)


@dataclass
class Scores:
    fields: dict[str, FieldScore] = field(default_factory=lambda: {f: FieldScore() for f in FIELDS})
    documents: int = 0

    @property
    def mean_f1(self) -> float:
        return sum(s.f1 for s in self.fields.values()) / len(self.fields)

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "mean_f1": round(self.mean_f1, 4),
            "fields": {
                name: {
                    "precision": round(s.precision, 4),
                    "recall": round(s.recall, 4),
                    "f1": round(s.f1, 4),
                    "tp": s.tp,
                    "fp": s.fp,
                    "fn": s.fn,
                }
                for name, s in self.fields.items()
            },
        }


def _party_items(record: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (norm(p.get("name")), norm(p.get("role")))
        for p in record.get("parties", [])
        if norm(p.get("name"))
    }


def _date_items(record: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (norm(d.get("label")), norm(d.get("date_text")))
        for d in record.get("key_dates", [])
        if norm(d.get("label"))
    }


def _match_obligations(
    predicted: list[dict[str, Any]], labelled: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Greedy one-to-one matches on obligor, deadline and shared words."""
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unused = list(predicted)
    for label in labelled:
        label_words = _words(label.get("obligation"))
        for candidate in unused:
            same_obligor = norm(candidate.get("obligor")) == norm(label.get("obligor"))
            same_deadline = norm(candidate.get("deadline")) == norm(label.get("deadline"))
            words = _words(candidate.get("obligation"))
            overlap = len(words & label_words) / max(1, len(label_words))
            if same_obligor and same_deadline and overlap >= 0.5:
                pairs.append((candidate, label))
                unused.remove(candidate)
                break
    return pairs


def score_document(scores: Scores, predicted: dict[str, Any], labelled: dict[str, Any]) -> None:
    scores.documents += 1
    scores.fields["parties"].add_sets(_party_items(predicted), _party_items(labelled))
    scores.fields["governing_law"].add_sets(
        {norm(predicted.get("governing_law"))} - {""}, {norm(labelled.get("governing_law"))} - {""}
    )
    scores.fields["key_dates"].add_sets(_date_items(predicted), _date_items(labelled))
    pairs = _match_obligations(predicted.get("obligations", []), labelled.get("obligations", []))
    obligations = scores.fields["obligations"]
    obligations.tp += len(pairs)
    obligations.fp += len(predicted.get("obligations", [])) - len(pairs)
    obligations.fn += len(labelled.get("obligations", [])) - len(pairs)
    evidence = scores.fields["obligation_evidence"]
    for candidate, label in pairs:
        wanted = {norm(e.get("locator")) for e in label.get("evidence", [])}
        got = {norm(e.get("locator")) for e in candidate.get("evidence", [])}
        if wanted & got:
            evidence.tp += 1
        else:
            evidence.fn += 1
            if got:
                evidence.fp += 1
    scores.fields["defined_terms"].add_sets(
        {norm(t.get("term")) for t in predicted.get("defined_terms", [])} - {""},
        {norm(t.get("term")) for t in labelled.get("defined_terms", [])} - {""},
    )
    scores.fields["risk_clauses"].add_sets(
        {norm(r.get("kind")) for r in predicted.get("risk_clauses", [])} - {""},
        {norm(r.get("kind")) for r in labelled.get("risk_clauses", [])} - {""},
    )
    scores.fields["classification"].add_sets(
        {norm(predicted.get("classification"))} - {""},
        {norm(labelled.get("classification"))} - {""},
    )


def summarise_kinds(documents: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(d.get("kind", "?") for d in documents))
