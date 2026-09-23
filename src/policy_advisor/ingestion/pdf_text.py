"""PDF -> text, with page tracking. Falls back to OCR automatically when a
PDF has no usable text layer (e.g. a scanned judgment), so callers don't need
to know in advance which extraction path a given file needs."""

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

PAGE_MARKER_RE = re.compile(r"\x01PAGE:(\d+)\x02")
_MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER = 20

# Recurring footer/watermark noise from this corpus's source PDFs - not legal
# content, but sits inline in the extracted text and otherwise pollutes
# chunks (and can run straight into the next word with no separator).
_NOISE_LINE_RE = re.compile(r"downloaded for free from|#sabilaw", re.IGNORECASE)


def make_page_marker(page_number: int) -> str:
    # Always end on a newline so two pages' text never run together into one
    # word at the boundary (e.g. "...SabiLaw" + "jurisdiction...").
    return f"\x01PAGE:{page_number}\x02\n"


def _drop_noise_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _NOISE_LINE_RE.search(line))


def strip_page_markers(text: str) -> str:
    """Page markers live inline in PdfText.text so page_at() can locate them
    by offset, but a rule/paragraph that spans a page break would otherwise
    get the literal marker bytes embedded mid-sentence in its chunk text -
    callers must strip them before using extracted text as chunk content."""
    return PAGE_MARKER_RE.sub("", text)


@dataclass
class PdfText:
    text: str

    def page_at(self, offset: int) -> int:
        page = 1
        for m in PAGE_MARKER_RE.finditer(self.text, 0, offset):
            page = int(m.group(1))
        return page


def _extract_with_pypdf(path: Path) -> PdfText:
    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        parts.append(make_page_marker(i))
        parts.append(_drop_noise_lines(page.extract_text() or ""))
    return PdfText(text="".join(parts))


def _looks_like_real_text(line: str) -> bool:
    """OCR on scanned legal documents picks up stamps/watermarks as short,
    mostly-punctuation lines (e.g. "; CT f - s£7T3699577 (c)"). Real body and
    heading text is long, or short but mostly letters."""
    if not line:
        return False
    if len(line) >= 40:
        return True
    letters = sum(c.isalpha() for c in line)
    return letters / len(line) >= 0.4


def _extract_with_ocr(path: Path, dpi: int = 300) -> PdfText:
    from pdf2image import convert_from_path
    import pytesseract

    images = convert_from_path(str(path), dpi=dpi)
    parts = []
    for i, image in enumerate(images, start=1):
        parts.append(make_page_marker(i))
        raw_text = pytesseract.image_to_string(image)
        cleaned_lines = [line for line in raw_text.splitlines() if _looks_like_real_text(line.strip())]
        parts.append(_drop_noise_lines("\n".join(cleaned_lines)))
    return PdfText(text="".join(parts))


def extract_pdf(path: Path) -> PdfText:
    pdf_text = _extract_with_pypdf(path)
    visible_chars = len(strip_page_markers(pdf_text.text).strip())
    page_count = len(PdfReader(str(path)).pages)
    if visible_chars < _MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER * page_count:
        return _extract_with_ocr(path)
    return pdf_text
