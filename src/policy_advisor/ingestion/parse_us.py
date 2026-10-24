"""Structural parsers for United States authority (docs/23 slice P2-B;
ADR-001 action 11). Three document families, three locator schemes from
`jurisdictions/data/locator_schemes.json`:

- Code of Federal Regulations text, sectioned as `§ 245.1 Heading` lines
  (what `scripts/fetch_us_corpus.py` derives from the eCFR XML, and what a
  lawyer's PDF of a CFR part extracts to). Locators `8 C.F.R. § 245.1(a)`.
- USCIS Policy Manual chapters as headed text (`## A. ...`, `### 1. ...`).
  Locators `USCIS-PM Vol. 7, Pt. B, Ch. 2.A.1`.
- A reported opinion as paged text (`[page 687 | Opinion of the Court]`),
  one chunk per reporter page so a pinpoint cite is a page the lawyer can
  open. Locators `533 U.S. 678, 687`.

Chunks are structural, never fixed windows (docs/03): a CFR paragraph is
never cut mid-sentence, and a long section is split only at its own lettered
and numbered paragraphs. Each chunk reports the character offset it starts
at so the caller can attach a physical page where there is one.

The `# key: value` header the corpus files carry is read by `read_header`;
callers pass the few facts the locators need (CFR title, manual volume and
chapter, reporter citation) so the same parser serves an upload without a
header when those facts can be inferred from the text.
"""

import re
from dataclasses import dataclass

HEADER_LINE_RE = re.compile(r"^# ([a-z_]+): (.*)$")
CFR_SECTION_RE = re.compile(r"^§ (\d+\.\d+)(?:-\d+\.\d+)? ?(.*)$", re.MULTILINE)
CFR_TITLE_CUE_RE = re.compile(r"\b(\d{1,2}) C\.?F\.?R\.?\b")
_LETTER_PARA_RE = re.compile(r"^\(([a-z])\)")
_NUMBER_PARA_RE = re.compile(r"^\((\d+)\)")
PM_HEADING_RE = re.compile(r"^(#{2,4}) (.*)$", re.MULTILINE)
PM_LABEL_RE = re.compile(r"^([A-Z]|\d+)\.\s")
REPORTER_PAGE_RE = re.compile(r"^\[page (\d+) \| ?(.*?)\]$", re.MULTILINE)
US_REPORTS_CITE_CUE_RE = re.compile(r"Cite as: (\d+) U\. ?S\. (\d+)")
# The first line of a bound-volume page: the citation, the term, or the case
# name. The line after it names the part of the opinion (Syllabus, Opinion of
# the Court, a Justice's dissent).
RUNNING_HEAD_RE = re.compile(
    r"^(Cite as: .*|OCTOBER TERM, \d{4}|[A-Z][A-Z .,'&-]+ v\. [A-Z][A-Z .,'&-]+)$"
)

# Below this many characters a lettered paragraph stays whole; above it, the
# parser splits at its numbered sub-paragraphs. Roughly 350 words, which is
# within what the embedding model reads before truncating.
CFR_SPLIT_THRESHOLD = 1800


@dataclass
class UsChunk:
    locator: str
    heading: str | None
    text: str
    start_offset: int
    # For opinions: the reporter page; for the others: 0 (no physical page).
    page: int = 0


def read_header(text: str) -> tuple[dict[str, str], int]:
    """The `# key: value` lines at the top of a corpus file, and the offset
    where the body starts."""
    header: dict[str, str] = {}
    offset = 0
    for line in text.splitlines(keepends=True):
        match = HEADER_LINE_RE.match(line.rstrip("\n"))
        if match:
            header[match.group(1)] = match.group(2).strip()
            offset += len(line)
        elif not line.strip() and header:
            offset += len(line)
            break
        else:
            break
    return header, offset


# --- Code of Federal Regulations -------------------------------------------------


def infer_cfr_title(text: str) -> int | None:
    """The CFR title number, from the header or the first `N CFR` mention."""
    header, _ = read_header(text)
    if header.get("cfr_title"):
        return int(header["cfr_title"])
    match = CFR_TITLE_CUE_RE.search(text)
    return int(match.group(1)) if match else None


def _split_paragraphs(
    body: str, base_offset: int, section_locator: str
) -> list[tuple[str, str, int]]:
    """(locator, text, start_offset) per top-level lettered paragraph; the
    text before `(a)` keeps the bare section locator. A lettered paragraph
    counts as top level only when it is the next letter in sequence, which
    keeps a roman `(i)` under `(h)(1)` from opening a new paragraph."""
    pieces: list[tuple[str, str, int]] = []
    current_locator = section_locator
    current_lines: list[str] = []
    current_start = base_offset
    expected = "a"
    offset = base_offset
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        match = _LETTER_PARA_RE.match(stripped)
        if match and match.group(1) == expected:
            if "".join(current_lines).strip():
                pieces.append((current_locator, "".join(current_lines), current_start))
            current_locator = f"{section_locator}({match.group(1)})"
            current_lines = [line]
            current_start = offset
            expected = chr(ord(expected) + 1)
        else:
            current_lines.append(line)
        offset += len(line)
    if "".join(current_lines).strip():
        pieces.append((current_locator, "".join(current_lines), current_start))
    return pieces


def _split_numbered(locator: str, text: str, start: int) -> list[tuple[str, str, int]]:
    pieces: list[tuple[str, str, int]] = []
    current_locator = locator
    current_lines: list[str] = []
    current_start = start
    offset = start
    expected = 1
    for line in text.splitlines(keepends=True):
        match = _NUMBER_PARA_RE.match(line.strip())
        if match and int(match.group(1)) == expected:
            if "".join(current_lines).strip():
                pieces.append((current_locator, "".join(current_lines), current_start))
            current_locator = f"{locator}({expected})"
            current_lines = [line]
            current_start = offset
            expected += 1
        else:
            current_lines.append(line)
        offset += len(line)
    if "".join(current_lines).strip():
        pieces.append((current_locator, "".join(current_lines), current_start))
    return pieces


def parse_cfr(text: str, cfr_title: int | None = None) -> list[UsChunk]:
    """Chunks for a CFR part. Reserved sections (`[Reserved]`) are skipped:
    there is nothing to cite."""
    title = cfr_title if cfr_title is not None else infer_cfr_title(text)
    if title is None:
        raise ValueError("cannot tell which CFR title this is; pass cfr_title")
    matches = list(CFR_SECTION_RE.finditer(text))
    chunks: list[UsChunk] = []
    for index, match in enumerate(matches):
        section = match.group(1)
        heading = match.group(2).strip() or None
        body_start = match.end() + 1
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        if not body.strip() or body.strip().lower().startswith("[reserved]"):
            continue
        section_locator = f"{title} C.F.R. § {section}"
        for locator, piece, start in _split_paragraphs(body, body_start, section_locator):
            if len(piece) > CFR_SPLIT_THRESHOLD and locator != section_locator:
                for sub_locator, sub_piece, sub_start in _split_numbered(locator, piece, start):
                    chunks.append(UsChunk(sub_locator, heading, sub_piece.strip(), sub_start))
            else:
                chunks.append(UsChunk(locator, heading, piece.strip(), start))
    return chunks


# --- USCIS Policy Manual -----------------------------------------------------------


def parse_uscis_pm(text: str, volume: int, part: str, chapter: int) -> list[UsChunk]:
    """One chunk per deepest heading. The text before the first `##` is the
    chapter's own chunk; a section's text before its first `###` is the
    section's. The Footnotes block is not a chunk: it is citations, not
    guidance, and its numbers are already inline as `[n]`."""
    base = f"USCIS-PM Vol. {volume}, Pt. {part}, Ch. {chapter}"
    _, body_start = read_header(text)
    matches = list(PM_HEADING_RE.finditer(text, body_start))
    chunks: list[UsChunk] = []

    def add(locator: str, heading: str | None, start: int, end: int) -> None:
        piece = text[start:end].strip()
        if piece:
            chunks.append(UsChunk(locator, heading, piece, start))

    first = matches[0].start() if matches else len(text)
    add(base, None, body_start, first)

    section_label: str | None = None
    for index, match in enumerate(matches):
        level = len(match.group(1))
        heading = match.group(2).strip()
        label_match = PM_LABEL_RE.match(heading)
        label = label_match.group(1) if label_match else None
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if heading.lower().startswith("footnotes"):
            break
        if level == 2:
            section_label = label
            locator = f"{base}.{label}" if label else base
        else:
            if section_label and label:
                locator = f"{base}.{section_label}.{label}"
            elif section_label:
                locator = f"{base}.{section_label}"
            else:
                locator = base
        add(locator, heading, match.end() + 1, end)
    return chunks


# --- Reported opinions -----------------------------------------------------------


def infer_reporter_citation(text: str) -> str | None:
    header, _ = read_header(text)
    if header.get("citation"):
        return header["citation"]
    match = US_REPORTS_CITE_CUE_RE.search(text)
    return f"{match.group(1)} U.S. {match.group(2)}" if match else None


def parse_us_reporter(text: str, citation: str | None = None) -> list[UsChunk]:
    """One chunk per reporter page of a paged opinion text. The running head
    ("Opinion of the Court", "KENNEDY, J., dissenting") becomes the heading,
    so a passage can be told apart from a dissent at a glance."""
    cite = citation or infer_reporter_citation(text)
    if cite is None:
        raise ValueError("cannot tell the reporter citation; pass citation")
    matches = list(REPORTER_PAGE_RE.finditer(text))
    chunks: list[UsChunk] = []
    for index, match in enumerate(matches):
        page = int(match.group(1))
        heading = match.group(2).strip() or None
        start = match.end() + 1
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        piece = text[start:end].strip()
        if piece:
            chunks.append(UsChunk(f"{cite}, {page}", heading, piece, start, page=page))
    return chunks


def split_running_head(page_text: str) -> tuple[str, str]:
    """(running head, body) for one page of a bound US Reports volume. The
    running head is the part-of-opinion line ("Opinion of the Court"); the
    citation or term line above it is dropped. Pages without one keep their
    whole text."""
    lines = [line.rstrip() for line in page_text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    head = ""
    if lines and RUNNING_HEAD_RE.match(lines[0].strip()):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and len(lines[0].strip()) < 60:
            head = lines.pop(0).strip()
    return head, "\n".join(lines).strip("\n\x0c ")


def paged_text_from_pages(pages: list[str], first_page: int) -> str:
    """The `[page N | head]` text `parse_us_reporter` reads, from the pages
    of a bound-volume PDF whose first page is reporter page `first_page`.
    Bound volumes carry no page numbers in their text layer, so the number is
    derived from position; `scripts/fetch_us_corpus.py` records that."""
    out: list[str] = []
    for index, page_text in enumerate(pages):
        head, body = split_running_head(page_text)
        out.append(f"[page {first_page + index} | {head}]")
        out.append(body)
        out.append("")
    return "\n".join(out).rstrip() + "\n"
