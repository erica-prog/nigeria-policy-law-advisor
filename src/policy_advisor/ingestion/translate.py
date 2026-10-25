"""Per-chunk translation at ingestion (CLAUDE-2.md capability 3, resolved
decision: document translation, not conversation-only). Runs once per chunk
at build time, like embedding - never per query. The original text is
always retained alongside the translation; nothing here ever replaces it."""

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from policy_advisor.config import get_settings
from policy_advisor.llm import chat_model, message_text
from policy_advisor.llm_retry import call_with_retry

LANGUAGE_NAMES = {"en": "English", "fr": "French"}

TRANSLATE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Translate the following legal text from {source_language} to {target_language}. Preserve all "
            "numbers, rule/order/paragraph references, party names, and deadlines exactly - a translation "
            "error in any of these could mislead a lawyer relying on it. Output only the translated text, "
            "nothing else.",
        ),
        ("human", "{text}"),
    ]
)


def get_translation_llm(api_key: str | None = None) -> ChatAnthropic:
    """`api_key` overrides the server key from settings (the web API passes the
    uploading user's own key); None keeps the previous behaviour."""
    settings = get_settings()
    # Retried explicitly in translate_text, so each attempt is visible.
    return chat_model(settings, api_key, max_tokens=2048, timeout=45)


def translate_text(
    llm: ChatAnthropic, text: str, source_language: str, target_language: str
) -> str:
    messages = TRANSLATE_PROMPT.format_messages(
        source_language=LANGUAGE_NAMES.get(source_language, source_language),
        target_language=LANGUAGE_NAMES.get(target_language, target_language),
        text=text,
    )
    return message_text(call_with_retry(lambda: llm.invoke(messages)))
