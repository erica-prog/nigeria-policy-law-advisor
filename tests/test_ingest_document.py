from pathlib import Path

import pytest

from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.ingestion.ingest_document import (
    SharedMatterReadOnlyError,
    UnsupportedDocumentError,
    add_document,
    remove_document,
)


def test_rejects_unsupported_format_before_touching_the_file():
    with pytest.raises(UnsupportedDocumentError):
        add_document("matter-a", Path("/nonexistent/contract.txt"))


def test_rejects_adding_to_the_shared_demo_matter_even_with_a_supported_format():
    # The read-only guard must fire before the unsupported-format check -
    # otherwise an unsupported file would mask the more important rejection.
    with pytest.raises(SharedMatterReadOnlyError):
        add_document(PHASE1_DEMO_MATTER_ID, Path("/nonexistent/contract.pdf"))


def test_rejects_removing_from_the_shared_demo_matter():
    with pytest.raises(SharedMatterReadOnlyError):
        remove_document(PHASE1_DEMO_MATTER_ID, "anything.pdf")
