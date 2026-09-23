"""Dispatches parsing by document type and attaches the metadata that makes
citations verifiable, jurisdiction filtering possible, and matters isolated
from each other (docs/03, CLAUDE-2.md capability 1)."""

from dataclasses import dataclass
from pathlib import Path

from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.ingestion.docx_text import extract_docx
from policy_advisor.ingestion.document_type import classify_document_type
from policy_advisor.ingestion.generic_chunk import parse_generic
from policy_advisor.ingestion.parse_judgment import parse_judgment
from policy_advisor.ingestion.parse_rules import parse_rules
from policy_advisor.ingestion.pdf_text import PdfText, extract_pdf

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
    DocumentSpec("Lagos-State-Magistrates-Courts-Civil-Procedure-Rules-2009.pdf", "statute", "lagos"),
    DocumentSpec("Meta v NDPC Terms & Consent Judgment.pdf", "judgment", "federal"),
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


def extract_document(path: Path) -> PdfText:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix == ".docx":
        return extract_docx(path)
    raise ValueError(f"Unsupported document format: {suffix} (supported: {sorted(SUPPORTED_SUFFIXES)})")


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


def chunk_document(spec: DocumentSpec, data_dir: Path) -> list[Chunk]:
    """Phase 1's hand-mapped demo corpus path - doc_type and jurisdiction are
    already known, so this skips classification."""
    pdf_text = extract_pdf(data_dir / spec.filename)
    return _build_chunks(pdf_text, spec.doc_type, spec.filename, PHASE1_DEMO_MATTER_ID, spec.jurisdiction)


def chunk_corpus(data_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for spec in CORPUS:
        chunks.extend(chunk_document(spec, data_dir))
    return chunks


def chunk_uploaded_document(matter_id: str, file_path: Path, jurisdiction: str | None = None) -> list[Chunk]:
    """The general ingestion path for any document a lawyer adds at runtime:
    extract -> classify -> chunk with the matching strategy -> tag with the
    matter it belongs to. Falls back to a generic recursive-split chunker
    (`doc_type="generic"`) for anything that isn't a recognized statute or
    judgment shape, so an unfamiliar document (e.g. a contract) never crashes
    ingestion - it just gets a less structurally-precise chunking strategy."""
    pdf_text = extract_document(file_path)
    doc_type = classify_document_type(pdf_text)
    return _build_chunks(pdf_text, doc_type, file_path.name, matter_id, jurisdiction)
