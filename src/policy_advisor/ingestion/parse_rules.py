"""Structural parser for statutory rules documents: split on "ORDER N" boundaries,
then split each Order's body into its numbered Rules (docs/03 - chunk on
structural boundaries, not fixed token windows, so a rule isn't separated
from its own sub-rules or cut mid-sentence)."""

import re
from dataclasses import dataclass

from policy_advisor.ingestion.numbered_blocks import split_numbered_blocks
from policy_advisor.ingestion.pdf_text import PdfText

ORDER_RE = re.compile(r"ORDER\s+(\d+)\s*\n+([A-Z][^\n]{0,120})")
RULE_START_RE = re.compile(r"^\s*(\d{1,3})\.\s")


@dataclass
class RuleChunk:
    order_number: int
    order_title: str
    rule_number: int
    part_number: int
    heading: str | None
    text: str
    page: int


def _drop_table_of_contents(order_matches: list[re.Match]) -> list[re.Match]:
    """These rules documents open with a table of contents that repeats every
    "ORDER N" heading (with one-line rule descriptions instead of full text)
    before the real body restarts numbering at Order 1. Keep only matches
    from the last "Order 1" onward - the TOC's own duplicate of every later
    Order is dropped along with it."""
    last_order_one = None
    for idx, match in enumerate(order_matches):
        if int(match.group(1)) == 1:
            last_order_one = idx
    if last_order_one is None:
        return order_matches
    return order_matches[last_order_one:]


def parse_rules(pdf: PdfText) -> list[RuleChunk]:
    chunks: list[RuleChunk] = []
    order_matches = _drop_table_of_contents(list(ORDER_RE.finditer(pdf.text)))

    for idx, order_match in enumerate(order_matches):
        order_number = int(order_match.group(1))
        order_title = order_match.group(2).strip()
        body_start = order_match.end()
        body_end = order_matches[idx + 1].start() if idx + 1 < len(order_matches) else len(pdf.text)
        order_body = pdf.text[body_start:body_end]

        # Some Orders are subdivided into lettered parts (A, B, C...) that
        # each restart rule numbering from 1 - e.g. Order 7 and Order 12 in
        # the FHC rules both have two separate "Rule 2"s. Detect a restart
        # (the rule number doesn't strictly increase) and tag the part, so
        # "Order 7 Rule 2" still resolves to one specific rule rather than
        # silently colliding with a different one under the same locator.
        part_number = 1
        last_rule_number = 0
        for block in split_numbered_blocks(order_body, RULE_START_RE, base_offset=body_start):
            if block.number <= last_rule_number:
                part_number += 1
            last_rule_number = block.number

            chunks.append(
                RuleChunk(
                    order_number=order_number,
                    order_title=order_title,
                    rule_number=block.number,
                    part_number=part_number,
                    heading=block.heading,
                    text=block.text,
                    page=pdf.page_at(block.start_offset),
                )
            )
    return chunks
