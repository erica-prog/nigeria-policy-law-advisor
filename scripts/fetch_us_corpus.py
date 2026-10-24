"""Fetch the public-domain United States texts behind the `us-library`
collection into `data/us/` (docs/23 slice P2-B, decision D8), and record
where each came from.

Three sources, three plain-text files the parsers in
`policy_advisor.ingestion.parse_us` read:

  8-cfr-part-245.txt            eCFR versioner API (XML -> sectioned text)
  uscis-pm-vol7-part-b-ch<N>.txt  USCIS Policy Manual chapter pages (HTML -> headed text)
  zadvydas-v-davis-533-us-678.txt  Library of Congress bound US Reports PDF -> paged text

Every file starts with `# key: value` header lines (source id, URL, retrieval
time, licence, and the facts the parser needs such as the CFR title or the
reporter's first page). `manifest.json` repeats them with the SHA-256 of the
raw bytes that were fetched, so a re-fetch can show exactly what changed.

The raw XML, HTML and PDF are not committed: they are large and the derived
text is what the index is built from. Re-run this script to refresh; commit
the diff with the new manifest.

Usage:
  uv run python -m scripts.fetch_us_corpus [--out data/us] [--pm-chapters 1,2,3]

Network only; never run in CI. The texts are US government works (public
domain), see data/us/PROVENANCE.md.
"""

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from policy_advisor.ingestion.parse_us import paged_text_from_pages

USER_AGENT = "policy-advisor-corpus-fetch/0.1 (+https://github.com/erica-prog)"
PUBLIC_DOMAIN = "US government work, public domain (17 U.S.C. § 105)"

ECFR_DATE = "2026-10-01"
ECFR_URL = f"https://www.ecfr.gov/api/versioner/v1/full/{ECFR_DATE}/title-8.xml?part=245"
ECFR_HUMAN_URL = "https://www.ecfr.gov/current/title-8/part-245"

PM_URL = "https://www.uscis.gov/policy-manual/volume-7-part-b-chapter-{n}"

OPINION = {
    "file": "zadvydas-v-davis-533-us-678.txt",
    "title": "Zadvydas v. Davis, 533 U.S. 678 (2001)",
    "citation": "533 U.S. 678",
    "first_page": 678,
    "url": "https://tile.loc.gov/storage-services/service/ll/usrep/usrep533/usrep533678/usrep533678.pdf",
    "human_url": "https://www.loc.gov/item/usrep533678/",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _get(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            import gzip

            data = gzip.decompress(data)
        return data


def _header(fields: dict[str, object]) -> str:
    return "".join(f"# {key}: {value}\n" for key, value in fields.items()) + "\n"


# --- eCFR -------------------------------------------------------------------


def _element_text(element: ElementTree.Element) -> str:
    return html.unescape("".join(element.itertext())).strip()


def convert_ecfr(xml_bytes: bytes, retrieved_at: str) -> tuple[str, dict]:
    root = ElementTree.fromstring(xml_bytes)
    head = root.find("HEAD")
    part_title = _element_text(head) if head is not None else "PART 245"
    lines: list[str] = []
    for section in root.iter("DIV8"):
        number = section.get("N", "")
        heading_el = section.find("HEAD")
        heading = _element_text(heading_el) if heading_el is not None else ""
        heading = re.sub(r"^§\s*[\d.\-]+\s*", "", heading)
        lines.append(f"§ {number} {heading}".rstrip())
        for child in section:
            if child.tag in ("HEAD", "CITA", "EDNOTE"):
                continue
            text = _element_text(child)
            if text:
                lines.append(re.sub(r"\s+", " ", text))
        lines.append("")
    meta = {
        "title": f"8 CFR Part 245 — {part_title.replace('PART 245—', '').strip()}",
        "source_id": "us-ecfr",
        "source_url": ECFR_HUMAN_URL,
        "api_url": ECFR_URL,
        "cfr_title": 8,
        "version_date": ECFR_DATE,
        "retrieved_at": retrieved_at,
        "licence": PUBLIC_DOMAIN,
    }
    return _header(meta) + "\n".join(lines).rstrip() + "\n", meta


# --- USCIS Policy Manual ------------------------------------------------------


class _GuidanceText(HTMLParser):
    """Turns the chapter's guidance tab into headed text: `## A. ...` for
    h2, `### 1. ...` for h3, one paragraph per block element."""

    BLOCK = {"p", "li", "tr", "div", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buffer: list[str] = []
        self._heading: str | None = None
        self._skip = 0

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer = []
        if not text:
            return
        if self._heading:
            self.lines.append(f"{self._heading} {text}")
        else:
            self.lines.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("h2", "h3", "h4"):
            self._flush()
            self._heading = {"h2": "##", "h3": "###", "h4": "####"}[tag]
        elif tag in self.BLOCK or tag == "br":
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag in ("h2", "h3", "h4"):
            self._flush()
            self._heading = None
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._buffer.append(data)


def convert_policy_manual(
    html_bytes: bytes, chapter: int, url: str, retrieved_at: str
) -> tuple[str, dict]:
    page = html_bytes.decode("utf-8", errors="replace")
    title_match = re.search(r"<title>(.*?)</title>", page, re.S)
    page_title = html.unescape(title_match.group(1)).split("|")[0].strip() if title_match else ""
    current = re.search(r"current-date__value\">\s*([^<]+)<", page)
    current_as_of = current.group(1).strip() if current else ""
    start = page.index(">", page.index("tabcontent--guidance")) + 1
    end = page.find("tabcontent--resources", start)
    segment = page[start : end if end > 0 else None]
    parser = _GuidanceText()
    parser.feed(segment)
    parser.close()
    lines = parser.lines
    # The page repeats the chapter navigation after the body; keep the text
    # up to and including the footnotes block.
    meta = {
        "title": f"USCIS Policy Manual, Volume 7, Part B, {page_title}",
        "source_id": "us-uscis-policy-manual",
        "source_url": url,
        "volume": 7,
        "part": "B",
        "chapter": chapter,
        "current_as_of": current_as_of,
        "retrieved_at": retrieved_at,
        "licence": PUBLIC_DOMAIN,
    }
    return _header(meta) + "\n".join(lines).rstrip() + "\n", meta


# --- US Reports (Library of Congress) -----------------------------------------


def convert_us_reports(pdf_bytes: bytes, spec: dict, retrieved_at: str) -> tuple[str, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "opinion.pdf"
        pdf_path.write_bytes(pdf_bytes)
        info = subprocess.run(
            ["pdfinfo", str(pdf_path)], capture_output=True, text=True, check=True
        ).stdout
        pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
        page_texts = []
        for n in range(1, pages + 1):
            out = subprocess.run(
                ["pdftotext", "-f", str(n), "-l", str(n), str(pdf_path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            page_texts.append(out)
    first_page = int(spec["first_page"])
    paged = paged_text_from_pages(page_texts, first_page)
    meta = {
        "title": spec["title"],
        "source_id": "us-reports-loc",
        "source_url": spec["human_url"],
        "pdf_url": spec["url"],
        "citation": spec["citation"],
        "first_page": first_page,
        "last_page": first_page + pages - 1,
        "page_derivation": "PDF page n is reporter page first_page + n - 1 (the bound volume's "
        "running heads carry no page numbers in the text layer); checked against the syllabus "
        "cross-references",
        "retrieved_at": retrieved_at,
        "licence": PUBLIC_DOMAIN,
    }
    return _header(meta) + paged, meta


# --- driver ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default="data/us")
    parser.add_argument("--pm-chapters", default="1,2,3")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}

    retrieved_at = _now()
    raw = _get(ECFR_URL)
    text, meta = convert_ecfr(raw, retrieved_at)
    (out / "8-cfr-part-245.txt").write_text(text, encoding="utf-8")
    manifest["8-cfr-part-245.txt"] = {
        **meta,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
    }
    print("wrote 8-cfr-part-245.txt")

    for chapter in [int(c) for c in args.pm_chapters.split(",") if c.strip()]:
        url = PM_URL.format(n=chapter)
        retrieved_at = _now()
        raw = _get(url)
        text, meta = convert_policy_manual(raw, chapter, url, retrieved_at)
        name = f"uscis-pm-vol7-part-b-ch{chapter}.txt"
        (out / name).write_text(text, encoding="utf-8")
        manifest[name] = {
            **meta,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": len(raw),
        }
        print(f"wrote {name}")
        time.sleep(1.0)  # polite to uscis.gov

    retrieved_at = _now()
    raw = _get(OPINION["url"])
    text, meta = convert_us_reports(raw, OPINION, retrieved_at)
    (out / OPINION["file"]).write_text(text, encoding="utf-8")
    manifest[OPINION["file"]] = {
        **meta,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
    }
    print(f"wrote {OPINION['file']}")

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("wrote manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
