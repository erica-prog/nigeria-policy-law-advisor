"""Turns pipeline output into source references (docs/contracts/web-api.md).
A citation is emitted only when a cited locator resolves to a chunk that was
retrieved for that request, using the same exact-or-prefix rule as
faithfulness.py, so nothing the model did not see can become a citation."""

from policy_advisor.api.schemas import SourceKind, SourceReference
from policy_advisor.generation.faithfulness import extract_cited_locators

AUTHORITY_DOC_TYPES = {"statute", "judgment"}


def kind_for_doc_type(doc_type: str | None) -> SourceKind:
    return "authority" if doc_type in AUTHORITY_DOC_TYPES else "evidence"


def chunk_reference(chunk, ref_id: str, cited_as: str | None = None) -> SourceReference:
    meta = chunk.metadata
    page = meta.get("page")
    jurisdiction = meta.get("jurisdiction") or None
    return SourceReference(
        id=ref_id,
        kind=kind_for_doc_type(meta.get("doc_type")),
        document=str(meta.get("source_document", "")),
        locator=meta.get("locator"),
        cited_as=cited_as,
        page=int(page) if isinstance(page, int | float) else None,
        quote=chunk.text,
        url=None,
        doc_type=meta.get("doc_type"),
        jurisdiction=jurisdiction,
    )


def web_reference(citation, ref_id: str) -> SourceReference:
    return SourceReference(
        id=ref_id,
        kind="web",
        document=citation.title or citation.url,
        locator=None,
        cited_as=None,
        page=None,
        quote=None,  # the fallback records URLs, not verified spans
        url=citation.url,
        doc_type="web",
        jurisdiction=None,
    )


def find_chunk_for_locator(cited: str, chunks):
    """Exact locator match first, then the longest retrieved locator the citation
    starts with (a sub-rule pinpoint inside a retrieved rule)."""
    for chunk in chunks:
        if chunk.metadata.get("locator") == cited:
            return chunk
    best = None
    for chunk in chunks:
        locator = chunk.metadata.get("locator") or ""
        if locator and cited.startswith(locator):
            if best is None or len(locator) > len(best.metadata.get("locator") or ""):
                best = chunk
    return best


def resolve_locators(cited_locators: list[str], chunks, prefix: str = "c") -> list[SourceReference]:
    references: list[SourceReference] = []
    seen: set[str] = set()
    for cited in cited_locators:
        if cited in seen:
            continue
        seen.add(cited)
        chunk = find_chunk_for_locator(cited, chunks)
        if chunk is not None:
            references.append(
                chunk_reference(chunk, f"{prefix}{len(references) + 1}", cited_as=cited)
            )
    return references


def citations_from_answer(answer_text: str, chunks) -> list[SourceReference]:
    return resolve_locators(extract_cited_locators(answer_text), chunks)


def retrieved_references(chunks) -> list[SourceReference]:
    return [chunk_reference(chunk, f"r{i + 1}") for i, chunk in enumerate(chunks)]
