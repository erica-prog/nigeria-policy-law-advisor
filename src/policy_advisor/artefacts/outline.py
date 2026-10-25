"""The outline: what the model writes and both renderers read (docs/35).

A document is sections of paragraphs. Each paragraph names the locators or
URLs that support it; a paragraph with no sources is allowed only when it
is the lawyer's own instruction restated (facts, scope, conclusions of the
brief) and must say so with `kind: "instruction"`. The model ends its run
with this as JSON; `parse_outline` tolerates a fenced block or prose around
it but nothing less than a valid object.
"""

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

ParagraphKind = Literal["authority", "instruction"]


class Paragraph(BaseModel):
    text: str = Field(min_length=1)
    kind: ParagraphKind = "authority"
    sources: list[str] = Field(
        default_factory=list, description="Locators or URLs shown by the tools, exactly as given"
    )


class Section(BaseModel):
    heading: str = Field(min_length=1)
    paragraphs: list[Paragraph] = Field(default_factory=list)


class Outline(BaseModel):
    title: str = Field(min_length=1)
    audience: str = ""
    summary: str = ""
    sections: list[Section] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=list,
        description="What the sources did not settle and what the lawyer must verify",
    )


OUTLINE_INSTRUCTIONS = """You are drafting for a practising lawyer. Use the tools to find authority (statutes, regulations, judgments, guidance, the firm's own precedents) and write the document as an outline. Every paragraph that states law must list, in `sources`, the exact locators or URLs the tools showed you; a paragraph that restates the lawyer's own instructions (the facts, the scope, the conclusion asked for) carries no sources and `kind: "instruction"`. Never cite a locator or URL the tools did not show; never present a fact of the case as authority; never invent a case, section or quotation. Put what the sources did not settle in `limitations`.

When you are done, end your reply with one JSON object and nothing after it, matching this schema exactly:
{"title": str, "audience": str, "summary": str, "sections": [{"heading": str, "paragraphs": [{"text": str, "kind": "authority" | "instruction", "sources": [str]}]}], "limitations": [str]}"""


class OutlineError(ValueError):
    pass


def parse_outline(text: str) -> Outline:
    """The last JSON object in `text`, validated."""
    candidates: list[str] = []
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates.extend(reversed(fenced))
    start = text.rfind('{"title"')
    if start < 0:
        start = text.find("{")
    if start >= 0:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : index + 1])
                    break
    for candidate in candidates:
        try:
            return Outline.model_validate(json.loads(candidate))
        except (json.JSONDecodeError, ValidationError):
            continue
    raise OutlineError("the drafting run did not end with a valid outline")
