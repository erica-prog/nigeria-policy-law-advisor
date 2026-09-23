"""Per-chunk translation at ingestion (CLAUDE-2.md capability 3, resolved
decision: document translation, not conversation-only). Runs once per chunk
at build time, like embedding - never per query. The original text is
always retained alongside the translation; nothing here ever replaces it."""

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from policy_advisor.config import get_settings

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


def get_translation_llm() -> ChatAnthropic:
    settings = get_settings()
    return ChatAnthropic(
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key.get_secret_value(),
        max_tokens=2048,
        default_request_timeout=45,
        max_retries=2,
    )


def translate_text(llm: ChatAnthropic, text: str, source_language: str, target_language: str) -> str:
    messages = TRANSLATE_PROMPT.format_messages(
        source_language=LANGUAGE_NAMES.get(source_language, source_language),
        target_language=LANGUAGE_NAMES.get(target_language, target_language),
        text=text,
    )
    return llm.invoke(messages).content
