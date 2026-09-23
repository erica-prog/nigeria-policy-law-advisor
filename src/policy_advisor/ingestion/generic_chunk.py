"""Fallback chunker for uploaded documents that don't match a recognized
statute or judgment structure (docs/03: "otherwise a semantic/recursive
splitter with meaningful overlap"). Used for arbitrary documents - contracts,
pleadings, anything without numbered Order/Rule or judgment-paragraph
structure - so ingestion never crashes on an unrecognized document shape."""

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from policy_advisor.ingestion.pdf_text import PdfText, strip_page_markers

_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=200)


@dataclass
class GenericChunk:
    chunk_index: int
    text: str
    page: int


def parse_generic(pdf: PdfText) -> list[GenericChunk]:
    raw_chunks = _SPLITTER.split_text(pdf.text)
    chunks = []
    cursor = 0
    chunk_index = 0
    for raw_chunk in raw_chunks:
        cleaned = strip_page_markers(raw_chunk).strip()
        if not cleaned:
            # A chunk that was nothing but a page marker (common right at the
            # start of a document) - skip it rather than emitting an empty
            # chunk with no content to embed or cite.
            continue
        # Page lookup only needs an approximate start offset here, not the
        # structural precision parse_rules/parse_judgment require - good
        # enough for a fallback chunker over unrecognized document shapes.
        probe = raw_chunk.strip()[:50]
        offset = pdf.text.find(probe, cursor) if probe else cursor
        if offset == -1:
            offset = cursor
        cursor = max(cursor, offset)
        chunks.append(GenericChunk(chunk_index=chunk_index, text=cleaned, page=pdf.page_at(offset)))
        chunk_index += 1
    return chunks
