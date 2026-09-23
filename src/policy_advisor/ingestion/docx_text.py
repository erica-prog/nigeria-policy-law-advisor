"""DOCX -> text, reusing the same PdfText/page-marker mechanism as PDFs so
downstream chunkers and page_at() lookups don't need format-specific logic.

Known limitation: DOCX has no fixed pagination without a layout/rendering
engine, so "page" here is a paragraph-block proxy (one "page" per N
paragraphs), not a literal printed page number - acceptable for citation
display, not exact like the PDF page numbers elsewhere in this corpus."""

from pathlib import Path

from docx import Document as DocxDocument

from policy_advisor.ingestion.pdf_text import PdfText, make_page_marker

_PARAGRAPHS_PER_PAGE_PROXY = 25


def extract_docx(path: Path) -> PdfText:
    document = DocxDocument(str(path))
    parts = []
    for i, paragraph in enumerate(document.paragraphs):
        if i % _PARAGRAPHS_PER_PAGE_PROXY == 0:
            parts.append(make_page_marker(i // _PARAGRAPHS_PER_PAGE_PROXY + 1))
        parts.append(paragraph.text + "\n")
    return PdfText(text="".join(parts))
