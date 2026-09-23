"""Grounding instructions for generation (docs/01): cite only retrieved text,
say so when context is insufficient instead of guessing, and don't conflate
statutory text with judgments interpreting it."""

from langchain_core.prompts import ChatPromptTemplate

# CLAUDE-2.md capability 3: the lawyer's chosen conversational language, never
# inferred silently - the UI asks for it explicitly (app.py) and it flows
# through here. Independent of which language a context passage happens to
# be written in, and independent of document-translation at ingestion - this
# only controls the language Claude responds in.
LANGUAGE_NAMES = {"en": "English", "fr": "French"}

SYSTEM_PROMPT = """You are a research aid for a practicing lawyer - not a substitute for their \
own judgment, and not a source of legal advice.

Respond in {response_language}, regardless of which language the context passages below are \
written in. Locator citations are never translated - keep them exactly as they appear in the \
context even when the surrounding sentence is in {response_language}.

Answer ONLY using the numbered context passages below. Follow these rules strictly:
1. Every substantive claim must cite the exact locator of the passage it came from, \
in the form [Source: <locator>] (e.g. [Source: Order 5 Rule 3] or [Source: Paragraph 3]).
2. If the passages don't contain enough information to answer, say so explicitly. \
Do not fall back on outside knowledge of Nigerian law.
3. Statutory rules state the law; judgments interpret or apply it. Never present a \
judgment's reasoning as if it were the text of a rule, or vice versa.
4. A Federal High Court rule and a Lagos State Magistrates' Court rule are not \
interchangeable authorities even when they cover the same topic - say which \
jurisdiction each cited passage belongs to if more than one is involved.

Context passages:
{context}
"""

PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", "{question}"),
    ]
)


def format_context(retrieved_chunks, response_language: str = "en") -> str:
    blocks = []
    for chunk in retrieved_chunks:
        meta = chunk.metadata
        header = (
            f"[{meta['locator']}] ({meta['doc_type']}, {meta['jurisdiction']}, "
            f"{meta['source_document']} p.{meta['page']})"
        )
        body = chunk.text
        translated = meta.get("translated_text")
        # Original text is always the only verified source for the citation
        # - the translation is included only as a reading aid when the
        # conversation is happening in the other language, never a
        # replacement for it (CLAUDE-2.md: never discard/displace the
        # original).
        if translated and meta.get("translated_language") == response_language and meta.get("language") != response_language:
            caveat = " - flagged as possibly inaccurate by the translation-fidelity check, verify against the original above" if meta.get("translation_flagged") else ""
            language_name = LANGUAGE_NAMES.get(response_language, response_language)
            body += f"\n\n({language_name} translation, for reading convenience only{caveat}):\n{translated}"
        blocks.append(f"{header}\n{body}")
    return "\n\n".join(blocks)
