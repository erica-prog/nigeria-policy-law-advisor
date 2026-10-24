"""Turns pipeline output into source references (docs/contracts/web-api.md).
A citation is emitted only when a cited locator resolves to a chunk that was
retrieved for that request, using the same exact-or-prefix rule as
faithfulness.py, so nothing the model did not see can become a citation."""

from policy_advisor.api.schemas import SourceKind, SourceReference
from policy_advisor.generation.faithfulness import extract_cited_locators
from policy_advisor.jurisdictions import UnknownJurisdiction, normalize_jurisdiction
from policy_advisor.jurisdictions.locators import best_support

AUTHORITY_DOC_TYPES = {"statute", "judgment"}


def kind_for_doc_type(doc_type: str | None) -> SourceKind:
    return "authority" if doc_type in AUTHORITY_DOC_TYPES else "evidence"


def trust_kind_of(meta: dict) -> str:
    """The stored trust label, or the doc-type rule for rows written before it existed."""
    return meta.get("trust_kind") or kind_for_doc_type(meta.get("doc_type"))


def kind_for_trust(trust_kind: str) -> SourceKind:
    """The client's three-way label: firm knowledge renders as authority."""
    if trust_kind in ("authority", "firm"):
        return "authority"
    if trust_kind == "web":
        return "web"
    return "evidence"


def chunk_reference(chunk, ref_id: str, cited_as: str | None = None) -> SourceReference:
    meta = chunk.metadata
    page = meta.get("page")
    jurisdiction = _canonical_jurisdiction(meta.get("jurisdiction"))
    trust_kind = trust_kind_of(meta)
    return SourceReference(
        id=ref_id,
        kind=kind_for_trust(trust_kind),
        trust_kind=trust_kind,  # type: ignore[arg-type]
        tier=meta.get("tier") or None,
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
        trust_kind="web",
        tier=None,
    )


def _canonical_jurisdiction(value) -> str | None:
    """Stored rows may hold a legacy alias; clients see the taxonomy id."""
    try:
        return normalize_jurisdiction(value)
    except UnknownJurisdiction:
        return str(value) if value else None


def find_chunk_for_locator(cited: str, chunks):
    """The retrieved chunk whose locator supports the citation under its
    locator scheme: an exact match first, else the longest supporting base
    (a pinpoint inside a retrieved rule). Same rule as faithfulness.py."""
    by_locator = {}
    for chunk in chunks:
        locator = chunk.metadata.get("locator")
        if locator and locator not in by_locator:
            by_locator[locator] = chunk
    supporting = best_support(cited, list(by_locator))
    return by_locator[supporting] if supporting is not None else None


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
