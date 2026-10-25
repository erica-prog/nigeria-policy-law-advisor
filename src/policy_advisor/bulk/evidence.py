"""Evidence spans: the extracted record must quote the document (docs/33).

For every `EvidenceRef` the model returned, find the chunk whose locator it
names and the quote inside that chunk's text, allowing for whitespace and
quote-mark differences. A found quote becomes a span (chunk id, locator,
character offsets); a quote that is not in the named chunk is kept but
marked unverified, and pulls the record's confidence down. Confidence is the
share of verified evidence, and a record with no evidence at all is 0: a
ledger with nothing to point at is a guess.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel

_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def normalise(text: str) -> str:
    return _WS.sub(" ", text.translate(_QUOTES)).strip().lower()


@dataclass(frozen=True)
class EvidenceSpan:
    path: str  # where in the record, e.g. "obligations[2].evidence[0]"
    locator: str
    quote: str
    chunk_id: str | None
    start: int | None
    end: int | None
    verified: bool


def _locate(quote: str, text: str) -> tuple[int, int] | None:
    """Offsets of `quote` in `text`, tolerant of whitespace and curly quotes;
    None when absent. Works on a normalised copy and maps back by walking
    the original."""
    target = normalise(quote)
    if not target:
        return None
    # Build normalised text with an index map to original offsets.
    mapped: list[int] = []
    out: list[str] = []
    previous_space = True
    for index, char in enumerate(text.translate(_QUOTES)):
        if char.isspace():
            if previous_space:
                continue
            out.append(" ")
            mapped.append(index)
            previous_space = True
        else:
            out.append(char.lower())
            mapped.append(index)
            previous_space = False
    haystack = "".join(out)
    position = haystack.find(target)
    if position < 0:
        return None
    start = mapped[position]
    end = mapped[min(position + len(target), len(mapped)) - 1] + 1
    return start, end


def _walk(value: Any, path: str) -> list[tuple[str, dict[str, Any]]]:
    """Every EvidenceRef-shaped dict in a record, with its path."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        if set(value) >= {"locator", "quote"} and isinstance(value.get("quote"), str):
            found.append((path, value))
            return found
        for key, item in value.items():
            found.extend(_walk(item, f"{path}.{key}" if path else key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_walk(item, f"{path}[{index}]"))
    return found


def verify_record(
    record: BaseModel, chunks: list[dict[str, Any]]
) -> tuple[list[EvidenceSpan], float]:
    """Spans for every evidence reference in `record`, and the confidence."""
    by_locator: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        locator = str(chunk.get("locator") or "")
        if locator:
            by_locator.setdefault(locator, []).append(chunk)
    spans: list[EvidenceSpan] = []
    for path, ref in _walk(record.model_dump(), ""):
        locator = str(ref.get("locator") or "").strip()
        quote = str(ref.get("quote") or "")
        candidates = by_locator.get(locator, [])
        hit: EvidenceSpan | None = None
        for chunk in candidates:
            offsets = _locate(quote, str(chunk.get("text") or ""))
            if offsets:
                hit = EvidenceSpan(
                    path, locator, quote, str(chunk.get("chunk_id")), offsets[0], offsets[1], True
                )
                break
        if hit is None:
            # The quote may be real but the locator wrong: look in every chunk
            # and record where it was, still unverified for the cited locator.
            chunk_id = None
            for chunk in chunks:
                if _locate(quote, str(chunk.get("text") or "")):
                    chunk_id = str(chunk.get("chunk_id"))
                    break
            hit = EvidenceSpan(path, locator, quote, chunk_id, None, None, False)
        spans.append(hit)
    if not spans:
        return [], 0.0
    verified = sum(1 for s in spans if s.verified)
    return spans, round(verified / len(spans), 3)


def spans_as_json(spans: list[EvidenceSpan]) -> list[dict[str, Any]]:
    return [asdict(s) for s in spans]
