"""United States parsers and the us-library collection (docs/23 slice P2-B,
AC-2): CFR sections, Policy Manual chapters, paged opinions, and the
read-only treatment every library collection gets."""

from pathlib import Path

import pytest

from policy_advisor.api.credentials import UserRecord
from policy_advisor.api.deps import resolve_matter_access
from policy_advisor.config import US_DATA_DIR, US_LIBRARY_MATTER_ID
from policy_advisor.ingestion import ingest_document, parse_us
from policy_advisor.ingestion.chunk import US_LIBRARY, _build_chunks, chunk_us_library
from policy_advisor.ingestion.document_type import classify_document_type
from policy_advisor.ingestion.pdf_text import PdfText, make_page_marker
from policy_advisor.jurisdictions.locators import parse_locator, scheme_of

CFR_TEXT = """# title: 8 CFR Part 245
# cfr_title: 8
# source_id: us-ecfr
# version_date: 2026-10-01

§ 245.1 Eligibility.
(a) General. Any alien physically present may apply.
(b) Restricted aliens. The following are ineligible:
(1) Any alien who entered in transit without a visa;
(2) Any alien who was a crewman.
§ 245.12-245.14 [Reserved]
§ 245.5 Medical examination.
An applicant shall have a medical examination.
"""


def test_cfr_sections_split_at_lettered_paragraphs_and_skip_reserved():
    chunks = parse_us.parse_cfr(CFR_TEXT)
    assert [c.locator for c in chunks] == [
        "8 C.F.R. § 245.1(a)",
        "8 C.F.R. § 245.1(b)",
        "8 C.F.R. § 245.5",
    ]
    assert chunks[0].heading == "Eligibility." and chunks[0].text.startswith("(a) General.")
    assert "(2) Any alien who was a crewman." in chunks[1].text  # sub-paragraphs stay attached
    assert all(scheme_of(c.locator) == "us_cfr" for c in chunks)


def test_long_lettered_paragraph_splits_at_numbered_subparagraphs(monkeypatch):
    monkeypatch.setattr(parse_us, "CFR_SPLIT_THRESHOLD", 40)
    chunks = parse_us.parse_cfr(CFR_TEXT)
    locators = [c.locator for c in chunks]
    assert "8 C.F.R. § 245.1(b)(1)" in locators and "8 C.F.R. § 245.1(b)(2)" in locators
    assert "8 C.F.R. § 245.1(b)" in locators  # the paragraph's own lead-in text


def test_cfr_title_is_inferred_from_the_text_when_there_is_no_header():
    text = "Title 8 CFR excerpt\n§ 245.5 Medical.\nText.\n§ 245.6 Interview.\nText.\n"
    assert parse_us.infer_cfr_title(text) == 8
    assert [c.locator for c in parse_us.parse_cfr(text)] == ["8 C.F.R. § 245.5", "8 C.F.R. § 245.6"]
    with pytest.raises(ValueError, match="cfr_title"):
        parse_us.parse_cfr("§ 245.5 Medical.\nText.\n")


def test_roman_numeral_i_is_not_mistaken_for_a_top_level_paragraph():
    text = "# cfr_title: 8\n\n§ 1.1 Test.\n(a) First.\n(i) a roman sub-item\n(b) Second.\n"
    assert [c.locator for c in parse_us.parse_cfr(text)] == [
        "8 C.F.R. § 1.1(a)",
        "8 C.F.R. § 1.1(b)",
    ]


PM_TEXT = """# volume: 7
# part: B
# chapter: 2
# current_as_of: October 23, 2026

Intro paragraph.
## A. Admission
Section A lead-in.
### 1. Inspection
Inspection text.
## B. Filing
Filing text.
## Footnotes
[1] A footnote.
"""


def test_policy_manual_chunks_per_deepest_heading_and_drops_footnotes():
    chunks = parse_us.parse_uscis_pm(PM_TEXT, 7, "B", 2)
    assert [(c.locator, c.heading) for c in chunks] == [
        ("USCIS-PM Vol. 7, Pt. B, Ch. 2", None),
        ("USCIS-PM Vol. 7, Pt. B, Ch. 2.A", "A. Admission"),
        ("USCIS-PM Vol. 7, Pt. B, Ch. 2.A.1", "1. Inspection"),
        ("USCIS-PM Vol. 7, Pt. B, Ch. 2.B", "B. Filing"),
    ]
    assert chunks[0].text == "Intro paragraph."
    assert all(scheme_of(c.locator) == "uscis_pm" for c in chunks)
    assert parse_locator("USCIS-PM Vol. 7, Pt. B, Ch. 2.A.1").pinpoint == ".A.1"


PAGES = [
    "OCTOBER TERM, 2000\n\nSyllabus\nZADVYDAS v. DAVIS ET AL.\nHeld: detention is limited.",
    "Cite as: 533 U. S. 678 (2001)\n\nOpinion of the Court\nJUSTICE BREYER delivered the opinion.",
    "ZADVYDAS v. DAVIS\nKENNEDY, J., dissenting\nI dissent.",
    "Cite as: 533 U. S. 678 (2001)\nKENNEDY, J., dissenting\nStill dissenting.",
]


def test_opinion_pages_become_reporter_pinpoints_with_running_heads():
    paged = parse_us.paged_text_from_pages(PAGES, 678)
    chunks = parse_us.parse_us_reporter(paged, "533 U.S. 678")
    assert [(c.locator, c.heading, c.page) for c in chunks] == [
        ("533 U.S. 678, 678", "Syllabus", 678),
        ("533 U.S. 678, 679", "Opinion of the Court", 679),
        ("533 U.S. 678, 680", "KENNEDY, J., dissenting", 680),
        ("533 U.S. 678, 681", "KENNEDY, J., dissenting", 681),
    ]
    assert chunks[1].text == "JUSTICE BREYER delivered the opinion."
    assert parse_locator("533 U.S. 678, 679").base == "533 U.S. 678"


def test_uploaded_bound_volume_pdf_is_paged_from_its_markers():
    text = "".join(make_page_marker(i + 1) + page + "\n" for i, page in enumerate(PAGES))
    pdf = PdfText(text=text)
    assert classify_document_type(pdf) == "us_opinion"
    chunks = _build_chunks(pdf, "us_opinion", "zadvydas.pdf", "case-1", "us-federal")
    assert [c.locator for c in chunks] == [f"533 U.S. 678, {p}" for p in (678, 679, 680, 681)]
    assert "\x01" not in chunks[1].text and chunks[1].page == 679
    assert chunks[0].chunk_id == "case-1::zadvydas.pdf::533US678678"


def test_classifier_recognises_cfr_text_and_leaves_nigerian_shapes_alone():
    assert classify_document_type(PdfText(text=CFR_TEXT)) == "us_cfr"
    nigerian = "ORDER 1 \nGENERAL\n1. First rule.\nORDER 2 \nSERVICE\n1. Second rule.\n"
    assert classify_document_type(PdfText(text=nigerian)) == "statute"


# --- the committed corpus ---


@pytest.mark.skipif(
    not all((US_DATA_DIR / s.filename).exists() for s in US_LIBRARY), reason="data/us not fetched"
)
def test_committed_us_library_chunks_carry_valid_locators_and_provenance():
    chunks = chunk_us_library(US_DATA_DIR)
    assert len(chunks) > 250
    expected_scheme = {"us_cfr": "us_cfr", "uscis_pm": "uscis_pm", "us_opinion": "us_reporter"}
    for chunk in chunks:
        assert chunk.locator_scheme == expected_scheme[chunk.doc_type], chunk.locator
        assert chunk.matter_id == US_LIBRARY_MATTER_ID
        assert chunk.tier == "authority" and chunk.trust_kind == "authority"
        assert chunk.country == "US" and chunk.jurisdiction == "us-federal"
        assert chunk.source_id in {"us-ecfr", "us-uscis-policy-manual", "us-reports-loc"}
        assert chunk.licence and "public domain" in chunk.licence
        assert chunk.text and "\x01" not in chunk.text
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    by_doc = {c.doc_type for c in chunks}
    assert by_doc == {"us_cfr", "uscis_pm", "us_opinion"}
    cfr = [c for c in chunks if c.doc_type == "us_cfr"]
    assert cfr[0].effective_date == "2026-10-01"
    opinion = [c for c in chunks if c.doc_type == "us_opinion"]
    assert opinion[0].locator == "533 U.S. 678, 678" and opinion[-1].locator == "533 U.S. 678, 725"


def test_golden_set_us_expectations_exist_in_the_corpus():
    import json

    questions = json.loads(Path("eval/golden_set_us.json").read_text(encoding="utf-8"))
    keys = {(c.source_document, c.locator) for c in chunk_us_library(US_DATA_DIR)}
    for question in questions:
        for expected in question["expected"]:
            assert (expected["source_document"], expected["locator"]) in keys, question["id"]
    assert sum(1 for q in questions if not q["expect_refusal"]) >= 10


# --- every library is read-only ---


def test_us_library_is_read_only_for_everyone_and_refuses_uploads(tmp_path):
    access = resolve_matter_access(US_LIBRARY_MATTER_ID, UserRecord("alice", "Alice"))
    assert access.read_only and access.owner is None
    with pytest.raises(ingest_document.SharedMatterReadOnlyError):
        ingest_document.add_document(US_LIBRARY_MATTER_ID, tmp_path / "x.pdf")
    with pytest.raises(ingest_document.SharedMatterReadOnlyError):
        ingest_document.delete_matter(US_LIBRARY_MATTER_ID)
