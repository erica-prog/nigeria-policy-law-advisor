"""Claude-as-judge faithfulness check (docs/06, CLAUDE-2.md capability 2): a
second Claude call that catches unsupported inference beyond what the cheap
citation-existence check (faithfulness.py) can see. Reserved for step
boundaries and the final synthesis in case reasoning, not every micro-step -
the resolved tiered-checking design trades latency for thoroughness only
where a step's output is about to become the next step's premise."""

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from policy_advisor.generation.case_reasoning_models import StepCheckOutcome
from policy_advisor.generation.case_reasoning_prompt import JUDGE_PROMPT


class JudgeVerdict(BaseModel):
    verdict: str = Field(description="Exactly 'ALLOWED' or 'FLAGGED'")
    notes: str = Field(description="One or two sentences explaining the verdict")


def judge_check(llm: ChatAnthropic, text: str, retrieved_chunks) -> StepCheckOutcome:
    authorities_text = "\n\n".join(f"[{c.metadata['locator']}]\n{c.text}" for c in retrieved_chunks)
    judge_llm = llm.with_structured_output(JudgeVerdict)
    messages = JUDGE_PROMPT.format_messages(authorities=authorities_text or "(none provided)", text=text)
    result: JudgeVerdict = judge_llm.invoke(messages)
    flagged = result.verdict.strip().upper() == "FLAGGED"
    return StepCheckOutcome(faithful=not flagged, judge_flagged=flagged, judge_notes=result.notes)
