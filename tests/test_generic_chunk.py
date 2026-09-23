from policy_advisor.ingestion.generic_chunk import parse_generic
from policy_advisor.ingestion.pdf_text import PdfText


def test_splits_long_text_into_multiple_chunks():
    paragraph = "This is a sentence about a contract term. " * 100
    pdf = PdfText(text=f"\x01PAGE:1\x02\n{paragraph}")
    chunks = parse_generic(pdf)

    assert len(chunks) > 1
    assert all(chunk.text.strip() for chunk in chunks)
    assert all("PAGE:" not in chunk.text for chunk in chunks)


def test_chunk_indices_are_sequential():
    paragraph = "Short clause text. " * 80
    pdf = PdfText(text=f"\x01PAGE:1\x02\n{paragraph}")
    chunks = parse_generic(pdf)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_assigns_page_numbers_across_a_page_break():
    # Each page's content is well over chunk_size + overlap, so some chunks
    # land fully within page 1 and others fully within page 2 regardless of
    # exactly where the splitter draws chunk boundaries.
    text = "\x01PAGE:1\x02\n" + ("Clause one text. " * 250) + "\x01PAGE:2\x02\n" + ("Clause two text. " * 250)
    pdf = PdfText(text=text)
    chunks = parse_generic(pdf)

    pages = {c.page for c in chunks}
    assert pages == {1, 2}
