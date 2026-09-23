from policy_advisor.ingestion.document_type import classify_document_type
from policy_advisor.ingestion.pdf_text import PdfText


def test_classifies_statute_from_repeated_order_headings():
    text = (
        "ORDER 1 \nGENERAL\n1. First rule.\n"
        "ORDER 2 \nSERVICE\n1. Second rule.\n"
    )
    assert classify_document_type(PdfText(text=text)) == "statute"


def test_classifies_judgment_from_paragraph_numbering_plus_cues():
    text = (
        "IN THE FEDERAL HIGH COURT OF NIGERIA\n"
        "BETWEEN\nAPPLICANT\nAND\nRESPONDENT\nJUDGMENT\n"
        "1. First paragraph of the judgment.\n"
        "2. Second paragraph of the judgment.\n"
        "3. Third paragraph of the judgment.\n"
    )
    assert classify_document_type(PdfText(text=text)) == "judgment"


def test_paragraph_numbering_without_judgment_cues_is_generic():
    """Numbered paragraphs alone aren't enough - a numbered list in a
    contract or memo shouldn't be misrouted to the judgment chunker."""
    text = (
        "1. The parties agree to the following terms.\n"
        "2. Payment shall be made within thirty days.\n"
        "3. This agreement is governed by the laws of Lagos State.\n"
    )
    assert classify_document_type(PdfText(text=text)) == "generic"


def test_unstructured_text_is_generic():
    text = "This is a short memo with no numbering or recognizable legal structure at all."
    assert classify_document_type(PdfText(text=text)) == "generic"
