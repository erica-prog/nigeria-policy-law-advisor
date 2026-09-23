from unittest.mock import patch

from policy_advisor.ingestion.chunk import Chunk
from policy_advisor.ingestion.chunk_translation import translate_chunks
from policy_advisor.ingestion.translation_fidelity import FidelityResult

MODULE = "policy_advisor.ingestion.chunk_translation"


def _chunk(text: str) -> Chunk:
    return Chunk(
        chunk_id="m::doc.pdf::Block0",
        text=text,
        source_document="doc.pdf",
        doc_type="generic",
        matter_id="m",
        jurisdiction=None,
        locator="Section 1",
        page=1,
    )


def test_translates_english_chunk_into_french_and_retains_original():
    original = _chunk("The defendant shall file a defence within five days.")

    with patch(f"{MODULE}.get_translation_llm", return_value="llm"), patch(
        f"{MODULE}.detect_language", return_value="en"
    ), patch(f"{MODULE}.translate_text", return_value="Le défendeur doit déposer une défense...") as translate_mock, patch(
        f"{MODULE}.check_translation_fidelity", return_value=FidelityResult(flagged=False)
    ):
        [result] = translate_chunks([original])

    translate_mock.assert_called_once_with("llm", original.text, "en", "fr")
    assert result.text == original.text  # original never overwritten
    assert result.language == "en"
    assert result.translated_language == "fr"
    assert result.translated_text == "Le défendeur doit déposer une défense..."
    assert result.translation_flagged is False


def test_translates_french_chunk_into_english():
    original = _chunk("Le défendeur doit déposer une défense dans un délai de cinq jours.")

    with patch(f"{MODULE}.get_translation_llm", return_value="llm"), patch(
        f"{MODULE}.detect_language", return_value="fr"
    ), patch(f"{MODULE}.translate_text", return_value="The defendant shall file a defence...") as translate_mock, patch(
        f"{MODULE}.check_translation_fidelity", return_value=FidelityResult(flagged=False)
    ):
        [result] = translate_chunks([original])

    translate_mock.assert_called_once_with("llm", original.text, "fr", "en")
    assert result.translated_language == "en"


def test_propagates_a_flagged_fidelity_check_onto_the_chunk():
    original = _chunk("The defendant shall file a defence within five days.")

    with patch(f"{MODULE}.get_translation_llm", return_value="llm"), patch(
        f"{MODULE}.detect_language", return_value="en"
    ), patch(f"{MODULE}.translate_text", return_value="some translation"), patch(
        f"{MODULE}.check_translation_fidelity",
        return_value=FidelityResult(flagged=True, reason="number mismatch"),
    ):
        [result] = translate_chunks([original])

    assert result.translation_flagged is True
    assert result.translation_flag_reason == "number mismatch"


def test_empty_input_returns_empty_without_loading_an_llm():
    with patch(f"{MODULE}.get_translation_llm") as get_llm_mock:
        assert translate_chunks([]) == []
    get_llm_mock.assert_not_called()
