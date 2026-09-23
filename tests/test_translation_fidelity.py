"""Tests the two-tier translation-fidelity check (CLAUDE-2.md capability 3).
The length-ratio tests and gating-logic tests are mocked/offline; the catch
tests at the bottom make real Claude calls deliberately, because the whole
point of this check is that it can see a subtle, length-preserving error a
heuristic can't - asserting that against a mock would only prove the wiring
runs, not that the check actually works."""

from unittest.mock import MagicMock

from policy_advisor.ingestion.translation_fidelity import (
    FidelityVerdict,
    check_translation_fidelity,
    judge_translation_fidelity,
    length_ratio_ok,
)


def test_length_ratio_ok_for_plausible_translation():
    assert length_ratio_ok("five days", "cinq jours") is True


def test_length_ratio_rejects_truncated_translation():
    original = "The defendant shall file a defence within five days of service of the summons upon him."
    truncated = "Le défendeur."
    assert length_ratio_ok(original, truncated) is False


def test_length_ratio_rejects_empty_translation():
    assert length_ratio_ok("some original text", "") is False


def test_check_skips_judge_call_when_length_ratio_already_fails():
    llm = MagicMock()
    result = check_translation_fidelity(llm, "a reasonably long original sentence here", "x", "en", "fr")

    assert result.flagged is True
    llm.with_structured_output.assert_not_called()


def test_check_calls_judge_when_length_ratio_passes():
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.return_value = FidelityVerdict(verdict="ALLOWED", reason="fine")

    result = check_translation_fidelity(llm, "five days notice", "cinq jours de préavis", "en", "fr")

    assert result.flagged is False
    llm.with_structured_output.assert_called_once()


# --- Real Claude calls below: proving the judge actually catches subtle,
# length-preserving errors, not just that the pipeline executes. ---


def test_judge_catches_an_injected_mistranslation_real_call():
    from policy_advisor.ingestion.translate import get_translation_llm

    original = "The defendant shall file a defence within five days of service of the summons."
    # Same length and structure as a correct translation - only the number
    # is silently wrong. This is exactly the class of error length_ratio_ok
    # cannot see.
    mistranslated = (
        "Le défendeur doit déposer une défense dans un délai de quinze jours "
        "à compter de la signification de l'assignation."
    )

    llm = get_translation_llm()
    result = judge_translation_fidelity(llm, original, mistranslated, "en", "fr")

    assert result.flagged is True


def test_judge_allows_a_correct_translation_real_call():
    from policy_advisor.ingestion.translate import get_translation_llm

    original = "The defendant shall file a defence within five days of service of the summons."
    correct = "Le défendeur doit déposer une défense dans un délai de cinq jours à compter de la signification de l'assignation."

    llm = get_translation_llm()
    result = judge_translation_fidelity(llm, original, correct, "en", "fr")

    assert result.flagged is False
