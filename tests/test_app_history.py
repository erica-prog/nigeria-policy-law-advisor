"""Runs the real app.py headlessly against seeded chat history.

The unit tests in test_chat_history.py prove the trust signals are stored. This
proves the page actually shows them again after a reload - a reloaded web
answer rendered as a normal bubble would look as checked as one grounded in the
lawyer's own documents, which is exactly what storing those fields is for.
"""

from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from policy_advisor.chat_history import ChatExchange, save_exchange
from policy_advisor.config import PHASE1_DEMO_MATTER_ID


@pytest.fixture
def rendered(fresh_db):
    save_exchange(
        "jdoe",
        PHASE1_DEMO_MATTER_ID,
        ChatExchange(
            question="What is the data protection fine?",
            answer="The Act sets a fine.",
            source="web",
            faithful=True,
            web_citations=[{"title": "NDPA 2023", "url": "https://ndpc.gov.ng/act"}],
        ),
    )
    save_exchange(
        "jdoe",
        PHASE1_DEMO_MATTER_ID,
        ChatExchange(
            question="Is service valid?",
            answer="Yes, per Order 9 Rule 9.",
            source="corpus",
            faithful=False,
            unsupported_citations=["Order 9 Rule 9"],
        ),
    )
    save_exchange(
        "asmith",
        PHASE1_DEMO_MATTER_ID,
        ChatExchange(question="asmith's private question", answer="x", source="corpus", faithful=True),
    )

    with patch("policy_advisor.auth.require_login", return_value=("jdoe", "J Doe")):
        app = AppTest.from_file("src/policy_advisor/app.py", default_timeout=120)
        app.run()
    assert not app.exception, app.exception
    return app


def _markdown(app) -> str:
    return " | ".join(element.value for element in app.markdown)


def test_a_reloaded_web_answer_is_still_marked_as_unverified(rendered):
    assert any("From an official source on the web" in c.value for c in rendered.caption)
    assert "https://ndpc.gov.ng/act" in _markdown(rendered)


def test_a_reloaded_failed_citation_check_still_warns(rendered):
    assert any("Order 9 Rule 9" in w.value for w in rendered.warning)


def test_only_this_lawyers_questions_appear_in_the_shared_matter(rendered):
    text = _markdown(rendered)
    assert "Is service valid?" in text
    assert "asmith's private question" not in text


def test_the_page_says_what_it_keeps(rendered):
    assert any("last 5 questions" in c.value for c in rendered.caption)
