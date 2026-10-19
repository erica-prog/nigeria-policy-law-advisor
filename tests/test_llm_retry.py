"""The shared Claude retry policy. Truncation is the case the deny-list grew
for: case-reasoning-invariants retried a max_tokens failure three times, and
every attempt died on the same missing `assessment` field."""

from types import SimpleNamespace

import pytest

from policy_advisor.llm_retry import OutputTruncated, call_with_retry, invoke_structured


def test_truncated_output_is_not_retried():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise OutputTruncated("cut off")

    with pytest.raises(OutputTruncated):
        call_with_retry(operation)
    assert calls == 1


def test_a_transient_error_is_retried():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("slow")
        return "ok"

    assert call_with_retry(operation) == "ok"
    assert calls == 3


def test_invoke_structured_turns_a_max_tokens_stop_into_truncation():
    structured = SimpleNamespace(
        invoke=lambda _messages: {
            "parsed": None,
            "parsing_error": ValueError("assessment missing"),
            "raw": SimpleNamespace(response_metadata={"stop_reason": "max_tokens"}),
        }
    )

    with pytest.raises(OutputTruncated):
        invoke_structured(structured, [])


def test_invoke_structured_returns_the_parsed_object():
    parsed = object()
    structured = SimpleNamespace(
        invoke=lambda _messages: {"parsed": parsed, "parsing_error": None, "raw": None}
    )

    assert invoke_structured(structured, []) is parsed
