from policy_advisor.ingestion.parse_rules import parse_rules
from policy_advisor.ingestion.pdf_text import PdfText


def test_table_of_contents_duplicate_is_dropped():
    """These rules documents open with a TOC that repeats every "ORDER N"
    heading before the real body restarts numbering at Order 1 - only the
    body's rules (with full text) should survive, not the TOC's one-liners."""
    text = (
        "ORDER 1 \nTOC TITLE\n"
        "1. one-line TOC description\n"
        "ORDER 2 \nANOTHER TOC TITLE\n"
        "1. another one-line TOC description\n"
        "ORDER 1 \nREAL TITLE\n"
        "1. The real, full body text of rule one.\n"
        "2. The real, full body text of rule two.\n"
    )
    chunks = parse_rules(PdfText(text=text))

    assert [(c.order_number, c.rule_number) for c in chunks] == [(1, 1), (1, 2)]
    assert "real, full body text of rule one" in chunks[0].text
    assert "TOC description" not in chunks[0].text


def test_assigns_page_number_from_marker_offset():
    text = "\x01PAGE:1\x02\nORDER 1 \nTITLE\n\x01PAGE:2\x02\n1. Body text on page two.\n"
    chunks = parse_rules(PdfText(text=text))

    assert len(chunks) == 1
    assert chunks[0].page == 2
    assert "PAGE:" not in chunks[0].text
