"""Translation-fidelity check at ingestion time (CLAUDE-2.md capability 3):
gates what a translated chunk is marked as in the long-term corpus, separate
from the per-step citation check that runs later at query time.

The cheap length-ratio heuristic only catches gross failures (truncation,
empty output) - a subtle error like a swapped number or locator leaves
length almost unchanged, so it would sail through a heuristic-only gate.
The judge call therefore isn't optional for translations that pass the
heuristic; it's the only check that can see a meaning-level error at all.
The heuristic still runs first because it's free and catches the cases
where asking the judge would be redundant."""

from dataclasses import dataclass

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

MIN_LENGTH_RATIO = 0.4
MAX_LENGTH_RATIO = 2.5


@dataclass
class FidelityResult:
    flagged: bool
    reason: str = ""


def length_ratio_ok(original: str, translated: str) -> bool:
    if not original.strip() or not translated.strip():
        return False
    ratio = len(translated) / len(original)
    return MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO


class FidelityVerdict(BaseModel):
    verdict: str = Field(description="Exactly 'ALLOWED' or 'FLAGGED'")
    reason: str = Field(description="One sentence explaining the verdict")


FIDELITY_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are checking whether a translation of a legal passage is accurate - especially numbers, "
            "rule/order/paragraph references, party names, and deadlines, where a translation error could "
            "mislead a lawyer relying on it. Answer 'FLAGGED' if you find any such error or significant "
            "meaning drift, 'ALLOWED' otherwise.",
        ),
        ("human", "Original ({source_language}):\n{original}\n\nTranslation ({target_language}):\n{translated}"),
    ]
)


def judge_translation_fidelity(
    llm: ChatAnthropic, original: str, translated: str, source_language: str, target_language: str
) -> FidelityResult:
    structured = llm.with_structured_output(FidelityVerdict)
    messages = FIDELITY_JUDGE_PROMPT.format_messages(
        original=original, translated=translated, source_language=source_language, target_language=target_language
    )
    result: FidelityVerdict = structured.invoke(messages)
    flagged = result.verdict.strip().upper() == "FLAGGED"
    return FidelityResult(flagged=flagged, reason=result.reason)


def check_translation_fidelity(
    llm: ChatAnthropic, original: str, translated: str, source_language: str, target_language: str
) -> FidelityResult:
    if not length_ratio_ok(original, translated):
        return FidelityResult(flagged=True, reason="translated length is implausible relative to the original")
    return judge_translation_fidelity(llm, original, translated, source_language, target_language)
