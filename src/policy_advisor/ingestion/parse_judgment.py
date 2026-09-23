"""Structural parser for judgments: split on top-level numbered paragraphs
("1.", "2.", ...), keeping decimal sub-paragraphs ("3.1.") attached to their
parent (docs/03 - chunk by section/paragraph, not a fixed window).

OCR'd judgments occasionally misrecognize a paragraph number (e.g. "2." as
"Zz."), which silently merges that paragraph into the previous one rather
than crashing - a known, accepted limitation of OCR-based ingestion, not a
parsing bug.

Court filings sometimes reproduce the same exhibit twice in one PDF (e.g. a
Terms of Settlement attached at the application stage, then reproduced again
as the certified judgment annex) - paragraph numbering restarts from 1 each
time, exactly like a statute's lettered sub-parts. Detected and tagged with
`part_number` the same way, so a locator still resolves to one paragraph."""

import re
from dataclasses import dataclass

from policy_advisor.ingestion.numbered_blocks import split_numbered_blocks
from policy_advisor.ingestion.pdf_text import PdfText

PARAGRAPH_START_RE = re.compile(r"^\s*(\d{1,3})\.\s+(?!\d)")


@dataclass
class ParagraphChunk:
    paragraph_number: int
    part_number: int
    heading: str | None
    text: str
    page: int


def parse_judgment(pdf: PdfText) -> list[ParagraphChunk]:
    blocks = split_numbered_blocks(pdf.text, PARAGRAPH_START_RE, base_offset=0)

    chunks = []
    part_number = 1
    last_paragraph_number = 0
    for block in blocks:
        if block.number <= last_paragraph_number:
            part_number += 1
        last_paragraph_number = block.number

        chunks.append(
            ParagraphChunk(
                paragraph_number=block.number,
                part_number=part_number,
                heading=block.heading,
                text=block.text,
                page=pdf.page_at(block.start_offset),
            )
        )
    return chunks
