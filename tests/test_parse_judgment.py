from policy_advisor.ingestion.parse_judgment import parse_judgment
from policy_advisor.ingestion.pdf_text import PdfText


def test_top_level_paragraphs_split_but_decimal_sub_paragraphs_stay_attached():
    text = (
        "BACKGROUND -\n"
        "1. On 20 September 2023, the Respondent issued a Notice of Investigation.\n"
        "3. The Final Orders include the following specific orders against the Applicant:\n"
        "3.1. Meta shall immediately seek express consent of data subjects.\n"
        "3.2. Meta shall carry out a Data Processing Impact Assessment.\n"
    )
    chunks = parse_judgment(PdfText(text=text))

    assert [c.paragraph_number for c in chunks] == [1, 3]
    assert chunks[0].heading == "BACKGROUND -"
    assert "3.1." in chunks[1].text
    assert "3.2." in chunks[1].text


def test_no_numbered_paragraphs_returns_no_chunks():
    chunks = parse_judgment(PdfText(text="Just narrative text with no numbering at all.\n"))
    assert chunks == []
