"""Language identification for chunks (CLAUDE-2.md capability 3). Cheap
heuristic library, not the embedding model itself - this only decides
whether a chunk needs translating and into which direction, so a wrong
guess on a handful of chunks is a translation-quality nuisance, not a
retrieval-correctness bug."""

from langdetect import LangDetectException, detect

SUPPORTED_LANGUAGES = {"en", "fr"}
DEFAULT_LANGUAGE = "en"


def detect_language(text: str) -> str:
    """Falls back to DEFAULT_LANGUAGE for text too short or ambiguous to
    classify (langdetect needs a reasonable amount of text), and for any
    language outside the two this phase has been validated against - the
    rest of the pipeline assumes the chunk is in its detected/original
    language either way, so a wrong default is conservative, not silent."""
    stripped = text.strip()
    if len(stripped) < 20:
        return DEFAULT_LANGUAGE
    try:
        detected = detect(stripped)
    except LangDetectException:
        return DEFAULT_LANGUAGE
    return detected if detected in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
