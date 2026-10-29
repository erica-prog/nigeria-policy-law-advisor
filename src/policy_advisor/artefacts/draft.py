"""The drafting run (docs/35): the research agent's loop, restricted to the
firm and authority tiers, ending in an outline; then the manifest, the
judge per section, the two renderings, the blob store and the artefacts row.

The agent sees no case evidence: its retrieval tools search the shared
libraries and the firm's own collections (`tier` firm) and the connectors.
The lawyer's instruction carries the facts, and paragraphs that restate it
are marked `instruction` in the outline. A paragraph that still manages to
cite an evidence chunk (a locator that happens to match one retrieved by a
connector-free path cannot, but a library locator shared with a case
document could) is rejected by the manifest and removed from the files.

Span-level faithfulness: the judge (`judge_check`) runs once per section
with the passages the section's paragraphs cite; a FLAGGED section is
recorded in the manifest and the files, never silently fixed.
"""

import asyncio
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from policy_advisor.agents.research import ResearchAgent
from policy_advisor.api.citations import find_chunk_for_locator
from anthropic.lib.tools import beta_async_tool
from pydantic import ValidationError

from policy_advisor.artefacts.outline import (
    OUTLINE_INSTRUCTIONS,
    Outline,
    validation_summary,
    parse_outline,
)
from policy_advisor.artefacts.provenance import Manifest, build_manifest
from policy_advisor.artefacts.render import render_docx, render_pptx
from policy_advisor.artefacts.store import (
    DEFAULT_MEMO_TEMPLATE,
    ArtefactRecord,
    Template,
    get_template,
    save_artefact,
)
from policy_advisor.config import Settings, get_settings
from policy_advisor.generation.judge_check import judge_check
from policy_advisor.llm import chat_model
from policy_advisor.logging_utils import get_logger, log_event
from policy_advisor.playbooks import Playbook
from policy_advisor.telemetry import attributed, span

Judge = Callable[
    [str, list[Any]], Any
]  # (section text, supporting chunks) -> StepCheckOutcome-like

DRAFT_PLAYBOOK = Playbook(
    id="draft-authority",
    name="Drafting (authority only)",
    practice_area="commercial",
    country="NG",
    jurisdictions=(),
    description="Memos and decks grounded in the firm's precedents and legal authority; case evidence is never cited as authority.",
    triggers=("draft",),
    mode="research",
    allowed_tools=(
        "search_authority",
        "search_firm_knowledge",
        "search_official_web",
        "search_ng_official",
        "fetch_ng_official",
        "cite_ng_official",
        "search_us_ecfr",
        "get_us_ecfr_text",
        "search_us_federal_register",
        "get_us_federal_register_document",
        "search",
        "search_document",
        "read_document",
        "get_endpoint_item",
        "searchGovInfo",
        "describePackageOrGranule",
    ),
    checklist=(
        "State the question the document answers and for whom, from the instruction.",
        "Find the governing authority first (statute, regulation, guidance), then judgments, then the firm's precedents.",
        "Write each paragraph of law with the locators or URLs the tools showed; restate the instruction's facts as instruction paragraphs without sources.",
        "Record under limitations what the sources did not settle and what the lawyer must verify.",
    ),
    extraction_schema={},
    output_sections=("Question", "Short answer", "Authority", "Analysis", "Next steps"),
    notice="Drafted from legal authority and the firm's precedents only; review before use.",
    attribution="Written for this project.",
)


def drafting_prompt(instruction: str, kind: str) -> str:
    """The user turn of a drafting run. A memo's sections are named here, in
    the outline instruction the model acts on: on the third nightly run
    (docs/39 §9a) they sat only in the system prompt, as headings for "the
    answer", and both memos used topic headings instead. A deck gets none
    until the lawyer review settles what a deck must hold."""
    prompt = f"{instruction}\n\n{OUTLINE_INSTRUCTIONS}"
    if kind in ("memo", "both"):
        prompt += (
            "\n\nGive the outline these sections, in this order and with these headings: "
            + "; ".join(DRAFT_PLAYBOOK.output_sections)
            + ". Where the analysis has several parts, give each part its own section in place"
            ' of the single Analysis section, headed "Analysis: " and the part\'s subject.'
        )
    return prompt


@dataclass
class DraftResult:
    artefact: ArtefactRecord
    outline: Outline
    manifest: Manifest


def default_judge(api_key: str | None, settings: Settings | None = None) -> Judge:
    settings = settings or get_settings()
    llm = chat_model(settings, api_key, max_tokens=1024, timeout=60)
    return lambda text, chunks: judge_check(llm, text, chunks)


class DraftingAgent:
    def __init__(
        self,
        research_agent: ResearchAgent,
        *,
        blob_store: Any,
        judge: Judge | None = None,
        settings: Settings | None = None,
        api_key: str | None = None,
    ) -> None:
        self._agent = research_agent
        self._blobs = blob_store
        self._settings = settings or get_settings()
        self._judge = judge or default_judge(api_key, self._settings)
        self._logger = get_logger("policy_advisor.artefacts", self._settings.log_level)

    def draft(
        self,
        instruction: str,
        *,
        matter_id: str,
        username: str | None,
        kind: str = "memo",
        template_id: str | None = None,
        jurisdiction: str | None = None,
        allow_web: bool = False,
    ) -> DraftResult:
        return asyncio.run(
            self.adraft(
                instruction,
                matter_id=matter_id,
                username=username,
                kind=kind,
                template_id=template_id,
                jurisdiction=jurisdiction,
                allow_web=allow_web,
            )
        )

    async def adraft(
        self,
        instruction: str,
        *,
        matter_id: str,
        username: str | None,
        kind: str,
        template_id: str | None,
        jurisdiction: str | None,
        allow_web: bool,
    ) -> DraftResult:
        template = (
            get_template(template_id or DEFAULT_MEMO_TEMPLATE) if kind in ("memo", "both") else None
        )
        with (
            attributed(username=username, matter_id=matter_id),
            span("artefact.draft", matter_id=matter_id, kind=kind),
        ):
            submitted: list[Outline] = []
            result = await self._agent.arun(
                drafting_prompt(instruction, kind),
                matter_id=matter_id,
                username=username,
                jurisdiction=jurisdiction,
                allow_web=allow_web,
                playbook=DRAFT_PLAYBOOK,
                authority_only=True,
                extra_tools=[submit_outline_tool(submitted)],
            )
            outline = submitted[-1] if submitted else parse_outline(result.answer)
            recorded = {r.url: r for r in getattr(result, "recorded", [])}
            manifest = build_manifest(outline, result.retrieved, recorded)
            self._judge_sections(outline, manifest, result.retrieved)
            docx_key = pptx_key = None
            if kind in ("memo", "both"):
                docx_key = self._store(
                    matter_id,
                    "docx",
                    render_docx(
                        outline,
                        manifest,
                        docx_key=template.docx_key if template else None,
                        style=template.style if template else {},
                        open_blob=self._blobs.open,
                    ),
                )
            if kind in ("slides", "both"):
                pptx_key = self._store(
                    matter_id,
                    "pptx",
                    render_pptx(
                        outline,
                        manifest,
                        style=(template.style if template and template.kind == "slides" else {}),
                    ),
                )
            artefact = save_artefact(
                matter_id=matter_id,
                kind=kind,
                title=outline.title,
                instruction=instruction,
                outline=outline.model_dump(),
                manifest=manifest.as_dict(),
                docx_key=docx_key,
                pptx_key=pptx_key,
                template_id=template.template_id if template else None,
                run_id=result.run_id,
                created_by=username,
            )
            log_event(
                self._logger,
                "artefact_drafted",
                artefact_id=artefact.artefact_id,
                matter_id=matter_id,
                kind=kind,
                run_id=result.run_id,
                **{f"manifest_{k}": v for k, v in manifest.counts().items()},
            )
            return DraftResult(artefact=artefact, outline=outline, manifest=manifest)

    def _judge_sections(self, outline: Outline, manifest: Manifest, chunks: list[Any]) -> None:
        """One judge call per section over the paragraphs the manifest kept,
        with the authority passages they cite. Rejected paragraphs are not
        judged: they are already out of the document, and their evidence
        chunks must not reach the judge as if they were authority."""
        rejected = manifest.rejected_paragraphs
        for s_index, section in enumerate(outline.sections):
            kept = [
                p
                for p_index, p in enumerate(section.paragraphs)
                if p.kind == "authority" and (s_index, p_index) not in rejected
            ]
            support = []
            seen: set[str] = set()
            for paragraph in kept:
                for locator in paragraph.sources:
                    chunk = find_chunk_for_locator(locator, chunks)
                    if chunk is None or locator in seen:
                        continue
                    trust = chunk.metadata.get("trust_kind") or "evidence"
                    if trust in ("authority", "firm"):
                        seen.add(locator)
                        support.append(chunk)
            text = "\n\n".join(p.text for p in kept)
            if not text.strip():
                continue
            try:
                outcome = self._judge(text, support)
                manifest.judge.append(
                    {
                        "section": s_index,
                        "verdict": "FLAGGED"
                        if getattr(outcome, "judge_flagged", False)
                        else "ALLOWED",
                        "notes": getattr(outcome, "judge_notes", ""),
                    }
                )
            except Exception as exc:  # the judge failing is recorded, never hidden
                manifest.judge.append(
                    {"section": s_index, "verdict": "UNAVAILABLE", "notes": str(exc)[:200]}
                )

    def _store(self, matter_id: str, suffix: str, data: bytes) -> str:
        import uuid

        key = f"{matter_id}/artefacts/{uuid.uuid4().hex}.{suffix}"
        with tempfile.NamedTemporaryFile(suffix=f".{suffix}", delete=False) as handle:
            handle.write(data)
            path = Path(handle.name)
        try:
            self._blobs.put(key, path)
        finally:
            path.unlink(missing_ok=True)
        return key


def template_for(template_id: str | None) -> Template | None:
    return get_template(template_id or DEFAULT_MEMO_TEMPLATE)


def submit_outline_tool(submitted: list[Outline]) -> Any:
    """The tool a drafting run hands its outline in through. The API returns
    tool input as well-formed JSON, which an outline written into the reply is
    not guaranteed to be (quotation marks in legal text break it). A rejected
    outline goes back to the model with the fields to fix."""

    async def submit_outline(**fields: Any) -> str:
        try:
            outline = Outline.model_validate(fields)
        except ValidationError as exc:
            return (
                "Outline rejected; fix these and call submit_outline again: "
                + validation_summary(exc)
            )
        submitted.append(outline)
        return "Outline recorded. Reply with one short sentence; do not repeat the outline."

    return beta_async_tool(
        submit_outline,
        name="submit_outline",
        description="Hand in the finished outline of the memo or deck. Call once when the research is done; call again only if it is rejected.",
        input_schema=Outline,
    )
