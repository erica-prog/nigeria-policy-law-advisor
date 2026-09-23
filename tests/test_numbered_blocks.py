import re

from policy_advisor.ingestion.numbered_blocks import split_numbered_blocks

RULE_START_RE = re.compile(r"^\s*(\d{1,3})\.\s")


def test_splits_on_each_rule_and_keeps_sub_rules_attached():
    text = (
        "Revocation of Civil Procedure Rules 2000\n"
        "1. The Federal High Court (Civil Procedure) Rules 2000 is hereby revoked.\n"
        "Citation and commencement\n"
        "2. These Rules may be cited as the Federal High Court (Civil Procedure) Rules 2009.\n"
        "(1) Sub-rule one.\n"
        "(2) Sub-rule two.\n"
    )
    blocks = split_numbered_blocks(text, RULE_START_RE)

    assert [b.number for b in blocks] == [1, 2]
    assert blocks[0].heading == "Revocation of Civil Procedure Rules 2000"
    assert blocks[1].heading == "Citation and commencement"
    assert "Sub-rule one" in blocks[1].text
    assert "Sub-rule two" in blocks[1].text


def test_heading_only_attaches_to_the_following_block_not_the_previous_one():
    text = (
        "1. First rule body.\n"
        "A short heading\n"
        "2. Second rule body.\n"
    )
    blocks = split_numbered_blocks(text, RULE_START_RE)

    assert blocks[0].heading is None
    assert "A short heading" not in blocks[0].text
    assert blocks[1].heading == "A short heading"


def test_long_or_punctuated_lines_are_not_mistaken_for_headings():
    text = (
        "1. First rule body, continuing on a second line that ends without punctuation\n"
        "and a third line that does end with a period.\n"
        "2. Second rule body.\n"
    )
    blocks = split_numbered_blocks(text, RULE_START_RE)

    assert blocks[0].heading is None
    assert "continuing on a second line" in blocks[0].text
    assert "third line that does end" in blocks[0].text


def test_start_offset_is_relative_to_base_offset():
    text = "1. Body.\n"
    blocks = split_numbered_blocks(text, RULE_START_RE, base_offset=100)
    assert blocks[0].start_offset == 100
