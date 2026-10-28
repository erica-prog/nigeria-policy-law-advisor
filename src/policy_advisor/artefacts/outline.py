"""The outline: what the model writes and both renderers read (docs/35).

A document is sections of paragraphs. Each paragraph names the locators or
URLs that support it; a paragraph with no sources is allowed only when it
is the lawyer's own instruction restated (facts, scope, conclusions of the
brief) and must say so with `kind: "instruction"`. The model hands the
outline in through the `submit_outline` tool, so the API returns it as
well-formed JSON; `parse_outline` reads a JSON object at the end of the reply
as a fallback for a run that never called the tool.
"""

import json
import re
from typing import Literal

from pydantic import Field, ValidationError

from policy_advisor.tool_input import ToolInputModel

ParagraphKind = Literal["authority", "instruction"]


class Paragraph(ToolInputModel):
    text: str = Field(min_length=1)
    kind: ParagraphKind = "authority"
    sources: list[str] = Field(
        default_factory=list, description="Locators or URLs shown by the tools, exactly as given"
    )


class Section(ToolInputModel):
    heading: str = Field(min_length=1)
    paragraphs: list[Paragraph] = Field(default_factory=list)


class Outline(ToolInputModel):
    title: str = Field(min_length=1)
    audience: str = ""
    summary: str = ""
    sections: list[Section] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=list,
        description="What the sources did not settle and what the lawyer must verify",
    )


OUTLINE_INSTRUCTIONS = """You are drafting for a practising lawyer. Use the tools to find authority (statutes, regulations, judgments, guidance, the firm's own precedents) and write the document as an outline. Every paragraph that states law must list, in `sources`, the exact locators or URLs the tools showed you; a paragraph that restates the lawyer's own instructions (the facts, the scope, the conclusion asked for) carries no sources and `kind: "instruction"`. Never cite a locator or URL the tools did not show; never present a fact of the case as authority; never invent a case, section or quotation. Put what the sources did not settle in `limitations`.

When you are done, call the submit_outline tool once with the whole outline: title, audience, summary, sections (each a heading and its paragraphs, each paragraph its text, kind "authority" or "instruction", and sources) and limitations. If it rejects the outline, fix what it names and call it again. If a tool says the budget is exhausted, call submit_outline at once with what you have and say in limitations what you could not check. After the outline is recorded, reply with one short sentence."""


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
    problems: list[str] = []
    for candidate in candidates:
        try:
            return Outline.model_validate(json.loads(candidate))
        except json.JSONDecodeError as exc:
            problems.append(f"JSON error at character {exc.pos}: {exc.msg}")
        except ValidationError as exc:
            problems.append(validation_summary(exc))
    detail = "; ".join(problems) if problems else "no JSON object in the reply"
    raise OutlineError(
        f"the drafting run did not end with a valid outline (reply of {len(text)} characters: {detail})"
    )


def validation_summary(exc: ValidationError, limit: int = 6) -> str:
    """Where and why an outline failed, never the text it contained."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()[:limit]
    )
