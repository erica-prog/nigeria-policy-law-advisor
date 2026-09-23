"""Infers a document's type from structural cues so arbitrary uploads can be
routed to the right chunker without a human pre-labeling each file (docs/03,
CLAUDE-2.md capability 1). A wrong classification means a wrong chunker means
bad citations, so this stays a small, explainable heuristic rather than a
black-box classifier - easy to audit when a document gets misrouted."""

import re

from policy_advisor.ingestion.parse_judgment import PARAGRAPH_START_RE
from policy_advisor.ingestion.parse_rules import ORDER_RE
from policy_advisor.ingestion.pdf_text import PdfText

_MIN_ORDER_MATCHES_FOR_STATUTE = 2
_MIN_PARAGRAPH_MATCHES_FOR_JUDGMENT = 3
_JUDGMENT_CUE_RE = re.compile(
    r"\bIN THE\b.{0,40}\bCOURT\b|\bBETWEEN\b|\bJUDGMENT\b|\bAPPLICANT\b|\bRESPONDENT\b"
    r"|\bPLAINTIFF\b|\bDEFENDANT\b|\bSUIT NO\b",
    re.IGNORECASE,
)


def _count_paragraph_starts(text: str) -> int:
    # PARAGRAPH_START_RE is anchored with `^` and matched per-line elsewhere
    # (numbered_blocks.split_numbered_blocks uses .match() on individual
    # lines, where `^` is implicitly the start of that line). Without
    # MULTILINE, running it against the whole multi-line text via
    # findall/search would only ever check position 0 of the entire string -
    # count per line instead to match how it's actually used elsewhere.
    return sum(1 for line in text.splitlines() if PARAGRAPH_START_RE.match(line))


def classify_document_type(pdf_text: PdfText) -> str:
    """Returns "statute", "judgment", or "generic" (the fallback for anything
    that doesn't match either recognized structure - e.g. a contract)."""
    order_matches = len(ORDER_RE.findall(pdf_text.text))
    if order_matches >= _MIN_ORDER_MATCHES_FOR_STATUTE:
        return "statute"

    paragraph_matches = _count_paragraph_starts(pdf_text.text)
    has_judgment_cues = bool(_JUDGMENT_CUE_RE.search(pdf_text.text))
    if paragraph_matches >= _MIN_PARAGRAPH_MATCHES_FOR_JUDGMENT and has_judgment_cues:
        return "judgment"

    return "generic"
