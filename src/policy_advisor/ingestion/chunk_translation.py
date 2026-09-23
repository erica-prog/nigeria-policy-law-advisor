"""Adds per-chunk language detection + translation (CLAUDE-2.md capability 3)
to an already-chunked document. A separate pass over finished chunks, not
part of structural parsing (parse_rules.py etc. stay language-agnostic) -
runs once at ingestion, like embedding, never per query."""

from dataclasses import replace

from policy_advisor.ingestion.chunk import Chunk
from policy_advisor.ingestion.language_detect import detect_language
from policy_advisor.ingestion.translate import get_translation_llm, translate_text
from policy_advisor.ingestion.translation_fidelity import check_translation_fidelity
from policy_advisor.logging_utils import get_logger, log_event

logger = get_logger(__name__)

_OTHER_LANGUAGE = {"en": "fr", "fr": "en"}


def translate_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Detects each chunk's language and translates it into the other of the
    two supported languages (English<->French), regardless of any matter's
    current conversational-language setting - ingestion happens once and is
    decoupled from a session preference that can change later."""
    if not chunks:
        return chunks

    llm = get_translation_llm()
    translated_chunks = []
    for chunk in chunks:
        language = detect_language(chunk.text)
        target_language = _OTHER_LANGUAGE[language]
        translated_text = translate_text(llm, chunk.text, language, target_language)
        fidelity = check_translation_fidelity(llm, chunk.text, translated_text, language, target_language)

        if fidelity.flagged:
            log_event(
                logger,
                "translation_fidelity_flagged",
                chunk_id=chunk.chunk_id,
                reason=fidelity.reason,
            )

        translated_chunks.append(
            replace(
                chunk,
                language=language,
                translated_text=translated_text,
                translated_language=target_language,
                translation_flagged=fidelity.flagged,
                translation_flag_reason=fidelity.reason,
            )
        )
    return translated_chunks
