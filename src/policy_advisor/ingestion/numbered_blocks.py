"""Shared structural splitter for numbered legal text (rules within an Order,
or paragraphs within a judgment). Used by parse_rules.py and parse_judgment.py
so the heading-attribution heuristic below isn't duplicated between them."""

import re
from dataclasses import dataclass

from policy_advisor.ingestion.pdf_text import strip_page_markers


@dataclass
class NumberedBlock:
    number: int
    heading: str | None
    text: str
    start_offset: int


def split_numbered_blocks(text: str, start_re: re.Pattern, base_offset: int = 0) -> list[NumberedBlock]:
    """Split `text` into blocks beginning at each `start_re` match (e.g. "12. ").
    Sub-numbering like "(1)"/"3.1." stays inside its parent block rather than
    starting a new one, since `start_re` only matches the top-level pattern.

    A short line with no trailing sentence punctuation, immediately before a
    match, is treated as that block's heading rather than the previous
    block's trailing content — legal drafting convention is that section
    headings aren't punctuated as sentences but body text is.
    """
    raw_lines = text.splitlines(keepends=True)
    stripped_lines = [line.rstrip("\n") for line in raw_lines]
    is_start = [bool(start_re.match(line)) for line in stripped_lines]

    blocks: list[NumberedBlock] = []
    pending_heading: list[str] = []
    current: dict | None = None
    offset = base_offset
    n = len(raw_lines)

    for i in range(n):
        stripped = stripped_lines[i]
        line_offset_abs = offset
        is_blank = not stripped.strip()

        if is_start[i]:
            if current is not None:
                blocks.append(_finalize(current))
            match = start_re.match(stripped)
            number = int(match.group(1))
            heading = " ".join(pending_heading).strip() or None
            pending_heading = []
            current = {
                "number": number,
                "heading": heading,
                "text": [stripped],
                "start_offset": line_offset_abs,
            }
        elif not is_blank:
            next_idx = i + 1
            while next_idx < n and not stripped_lines[next_idx].strip():
                next_idx += 1
            next_is_start = next_idx < n and is_start[next_idx]
            looks_like_heading = (
                next_is_start
                and len(stripped.strip()) < 100
                and not stripped.rstrip().endswith((".", ";", ":", ","))
            )
            if looks_like_heading:
                pending_heading.append(stripped.strip())
            elif current is not None:
                current["text"].append(stripped)
            # else: stray preface text before the first numbered block - discard

        offset += len(raw_lines[i])

    if current is not None:
        blocks.append(_finalize(current))
    return blocks


def _finalize(current: dict) -> NumberedBlock:
    text = "\n".join(part for part in current["text"] if part.strip())
    return NumberedBlock(
        number=current["number"],
        heading=current["heading"] and strip_page_markers(current["heading"]),
        text=strip_page_markers(text),
        start_offset=current["start_offset"],
    )
