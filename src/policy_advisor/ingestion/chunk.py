"""Dispatches parsing by document type and attaches the metadata that makes
citations verifiable, jurisdiction filtering possible, and matters isolated
from each other (docs/03, CLAUDE-2.md capability 1)."""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from policy_advisor.config import PHASE1_DEMO_MATTER_ID, US_DATA_DIR, US_LIBRARY_MATTER_ID
from policy_advisor.ingestion.document_type import classify_document_type
from policy_advisor.ingestion.docx_text import extract_docx
from policy_advisor.ingestion.generic_chunk import parse_generic
from policy_advisor.ingestion.parse_judgment import parse_judgment
from policy_advisor.ingestion.parse_rules import parse_rules
from policy_advisor.ingestion.parse_us import (
    REPORTER_PAGE_RE,
    UsChunk,
    infer_reporter_citation,
    paged_text_from_pages,
    parse_cfr,
    parse_us_reporter,
    parse_uscis_pm,
    read_header,
)
from policy_advisor.ingestion.pdf_text import (
    PAGE_MARKER_RE,
    PdfText,
    extract_pdf,
    strip_page_markers,
)
from policy_advisor.jurisdictions import load_taxonomy, normalize_jurisdiction
from policy_advisor.jurisdictions.locators import scheme_of

# United States authority families (parse_us.py); all authority, like
# statutes and judgments.
US_DOC_TYPES = frozenset({"us_cfr", "uscis_pm", "us_opinion"})
AUTHORITY_DOC_TYPES = frozenset({"statute", "judgment"}) | US_DOC_TYPES

SUPPORTED_SUFFIXES = {".pdf", ".docx"}


@dataclass
class DocumentSpec:
    filename: str
    doc_type: str  # "statute" | "judgment" - hand-mapped, Phase 1 demo corpus only
    jurisdiction: str | None


# Phase 1's original fixed corpus, preserved as a seed matter so the demo and
# its golden set keep working unchanged. New uploads go through
# chunk_uploaded_document() below instead of this hand-mapped list.
CORPUS: list[DocumentSpec] = [
    DocumentSpec("FHC Civil Procedure Rules.pdf", "statute", "federal"),
    DocumentSpec(
        "Lagos-State-Magistrates-Courts-Civil-Procedure-Rules-2009.pdf", "statute", "lagos"
    ),
    DocumentSpec("Meta v NDPC Terms & Consent Judgment.pdf", "judgment", "federal"),
]


@dataclass
class LibraryDocumentSpec:
    """A text file in a library collection: hand-mapped like the demo corpus,
    plus the registry source it came from (docs/24)."""

    filename: str
    doc_type: str
    jurisdiction: str
    source_id: str


# The United States federal library (docs/23 slice P2-B). Fetched by
# scripts/fetch_us_corpus.py; each file's header carries its provenance.
US_LIBRARY: list[LibraryDocumentSpec] = [
    LibraryDocumentSpec("8-cfr-part-245.txt", "us_cfr", "us-federal", "us-ecfr"),
    LibraryDocumentSpec(
        "uscis-pm-vol7-part-b-ch1.txt", "uscis_pm", "us-federal", "us-uscis-policy-manual"
    ),
    LibraryDocumentSpec(
        "uscis-pm-vol7-part-b-ch2.txt", "uscis_pm", "us-federal", "us-uscis-policy-manual"
    ),
    LibraryDocumentSpec(
        "uscis-pm-vol7-part-b-ch3.txt", "uscis_pm", "us-federal", "us-uscis-policy-manual"
    ),
    LibraryDocumentSpec(
        "zadvydas-v-davis-533-us-678.txt", "us_opinion", "us-federal", "us-reports-loc"
    ),
]


@dataclass
class Chunk:
    chunk_id: str
    text: str
    source_document: str
    doc_type: str
    matter_id: str
    jurisdiction: str | None
    locator: str
    page: int
    heading: str | None = None
    # Multi-language support (CLAUDE-2.md capability 3). `text` above is
    # always the original-language wording - translated_text never replaces
    # it, only sits alongside it. Populated by ingestion/chunk_translation.py
    # as a separate pass over already-built chunks, not here.
    language: str = "en"
    translated_text: str | None = None
    translated_language: str | None = None
    translation_flagged: bool = False
    translation_flag_reason: str = ""
    # Corpus tier and trust (ADR-001 decision 1). `tier` is the matter's tier;
    # `trust_kind` is what the client renders: statutes and judgments are
    # authority, everything a lawyer uploads about a case is evidence.
    tier: str = "matter"
    trust_kind: str = "evidence"
    # Which citation grammar the locator is written in (jurisdictions/locators.py).
    locator_scheme: str | None = None
    # Provenance for library and connector content; empty for uploads.
    source_id: str | None = None
    effective_date: str | None = None
    licence: str | None = None
    # Structured jurisdiction, derived from the taxonomy id in `jurisdiction`.
    country: str | None = None
    jurisdiction_level: str | None = None
    region: str | None = None


def _enrich(chunks: list[Chunk], tier: str) -> list[Chunk]:
    """Fill the derived fields every chunk carries, in one place."""
    for chunk in chunks:
        chunk.tier = tier
        chunk.trust_kind = "authority" if chunk.doc_type in AUTHORITY_DOC_TYPES else "evidence"
        chunk.locator_scheme = scheme_of(chunk.locator)
        j = load_taxonomy().resolve(chunk.jurisdiction)
        if j is not None:
            chunk.country, chunk.jurisdiction_level, chunk.region = j.country, j.level, j.region
    return chunks


def extract_document(path: Path) -> PdfText:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix == ".docx":
        return extract_docx(path)
    raise ValueError(
        f"Unsupported document format: {suffix} (supported: {sorted(SUPPORTED_SUFFIXES)})"
    )


def _build_chunks(
    pdf_text: PdfText, doc_type: str, filename: str, matter_id: str, jurisdiction: str | None
) -> list[Chunk]:
    if doc_type == "statute":
        chunks = []
        for rule in parse_rules(pdf_text):
            # part_number > 1 means rule numbering restarted within this
            # Order (a lettered sub-part) - disambiguate so the locator
            # still resolves to exactly one rule (see parse_rules.py).
            part_suffix = f"Part{rule.part_number}" if rule.part_number > 1 else ""
            locator_suffix = f" (Part {rule.part_number})" if rule.part_number > 1 else ""
            chunks.append(
                Chunk(
                    chunk_id=f"{matter_id}::{filename}::Order{rule.order_number}Rule{rule.rule_number}{part_suffix}",
                    text=rule.text,
                    source_document=filename,
                    doc_type=doc_type,
                    matter_id=matter_id,
                    jurisdiction=jurisdiction,
                    locator=f"Order {rule.order_number} Rule {rule.rule_number}{locator_suffix}",
                    page=rule.page,
                    heading=rule.heading,
                )
            )
        return chunks

    if doc_type == "judgment":
        chunks = []
        for para in parse_judgment(pdf_text):
            part_suffix = f"Part{para.part_number}" if para.part_number > 1 else ""
            locator_suffix = f" (Part {para.part_number})" if para.part_number > 1 else ""
            chunks.append(
                Chunk(
                    chunk_id=f"{matter_id}::{filename}::Para{para.paragraph_number}{part_suffix}",
                    text=para.text,
                    source_document=filename,
                    doc_type=doc_type,
                    matter_id=matter_id,
                    jurisdiction=jurisdiction,
                    locator=f"Paragraph {para.paragraph_number}{locator_suffix}",
                    page=para.page,
                    heading=para.heading,
                )
            )
        return chunks

    if doc_type in US_DOC_TYPES:
        return _build_us_chunks(pdf_text, doc_type, filename, matter_id, jurisdiction)

    if doc_type == "generic":
        return [
            Chunk(
                chunk_id=f"{matter_id}::{filename}::Block{block.chunk_index}",
                text=block.text,
                source_document=filename,
                doc_type=doc_type,
                matter_id=matter_id,
                jurisdiction=jurisdiction,
                locator=f"Section {block.chunk_index + 1}",
                page=block.page,
                heading=None,
            )
            for block in parse_generic(pdf_text)
        ]

    raise ValueError(f"Unknown doc_type: {doc_type}")


def _locator_slug(locator: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", locator)


def _iso_date(value: str | None) -> str | None:
    """`2026-10-01` or `October 23, 2026` -> ISO date; anything else -> None,
    because `effective_date` is a DATE column."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_us(pdf_text: PdfText, doc_type: str) -> tuple[list[UsChunk], dict[str, str]]:
    text = pdf_text.text
    header, _ = read_header(text)
    if doc_type == "us_cfr":
        return parse_cfr(text), header
    if doc_type == "uscis_pm":
        try:
            volume, part, chapter = int(header["volume"]), header["part"], int(header["chapter"])
        except KeyError:
            raise ValueError(
                "a USCIS Policy Manual text needs `volume`, `part` and `chapter` header lines"
            ) from None
        return parse_uscis_pm(text, volume, part, chapter), header
    # us_opinion: a corpus file is already paged; a PDF of a bound volume is
    # paged here from its page markers, numbering from the citation's page.
    if not REPORTER_PAGE_RE.search(text):
        citation = infer_reporter_citation(text)
        if citation is None:
            raise ValueError("cannot tell the reporter citation of this opinion")
        first_page = int(citation.rsplit(" ", 1)[1])
        pages = [strip_page_markers(p) for p in PAGE_MARKER_RE.split(text) if not p.isdigit()]
        text = paged_text_from_pages([p for p in pages if p.strip()], first_page)
        return parse_us_reporter(text, citation), header
    return parse_us_reporter(text), header


def _build_us_chunks(
    pdf_text: PdfText, doc_type: str, filename: str, matter_id: str, jurisdiction: str | None
) -> list[Chunk]:
    parsed, header = _parse_us(pdf_text, doc_type)
    effective = _iso_date(header.get("version_date") or header.get("current_as_of"))
    return [
        Chunk(
            chunk_id=f"{matter_id}::{filename}::{_locator_slug(c.locator)}",
            text=strip_page_markers(c.text),
            source_document=filename,
            doc_type=doc_type,
            matter_id=matter_id,
            jurisdiction=jurisdiction,
            locator=c.locator,
            page=c.page or pdf_text.page_at(c.start_offset),
            heading=c.heading,
            source_id=header.get("source_id") or None,
            effective_date=effective,
            licence=header.get("licence") or None,
        )
        for c in parsed
    ]


def chunk_library_document(
    spec: LibraryDocumentSpec, data_dir: Path, matter_id: str
) -> list[Chunk]:
    """A text file of a library collection: already structured by the fetch
    script, so no PDF extraction and no classification."""
    text = (data_dir / spec.filename).read_text(encoding="utf-8")
    chunks = _build_chunks(
        PdfText(text=text),
        spec.doc_type,
        spec.filename,
        matter_id,
        normalize_jurisdiction(spec.jurisdiction),
    )
    for chunk in chunks:
        chunk.source_id = chunk.source_id or spec.source_id
    return _enrich(chunks, tier="authority")


def chunk_us_library(data_dir: Path = US_DATA_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for spec in US_LIBRARY:
        chunks.extend(chunk_library_document(spec, data_dir, US_LIBRARY_MATTER_ID))
    return chunks


def chunk_document(spec: DocumentSpec, data_dir: Path) -> list[Chunk]:
    """Phase 1's hand-mapped demo corpus path - doc_type and jurisdiction are
    already known, so this skips classification."""
    pdf_text = extract_pdf(data_dir / spec.filename)
    chunks = _build_chunks(
        pdf_text,
        spec.doc_type,
        spec.filename,
        PHASE1_DEMO_MATTER_ID,
        normalize_jurisdiction(spec.jurisdiction),
    )
    # The demo corpus is the shared reference library: the authority tier.
    return _enrich(chunks, tier="authority")


def chunk_corpus(data_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for spec in CORPUS:
        chunks.extend(chunk_document(spec, data_dir))
    return chunks


def chunk_uploaded_document(
    matter_id: str, file_path: Path, jurisdiction: str | None = None
) -> list[Chunk]:
    """The general ingestion path for any document a lawyer adds at runtime:
    extract -> classify -> chunk with the matching strategy -> tag with the
    matter it belongs to. Falls back to a generic recursive-split chunker
    (`doc_type="generic"`) for anything that isn't a recognized statute or
    judgment shape, so an unfamiliar document (e.g. a contract) never crashes
    ingestion - it just gets a less structurally-precise chunking strategy."""
    pdf_text = extract_document(file_path)
    doc_type = classify_document_type(pdf_text)
    chunks = _build_chunks(
        pdf_text, doc_type, file_path.name, matter_id, normalize_jurisdiction(jurisdiction)
    )
    return _enrich(chunks, tier="matter")
