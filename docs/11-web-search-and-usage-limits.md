# 11 — Web Search Fallback and Usage Limits

> **Since Phase 2 (docs/24):** the domain allow-list is no longer a constant in `web_search.py`. It is the union of `config.domains` over the allowed `web_search` rows of the source registry, and an empty registry turns web search off.

Written after a live test session against the running app (`allow_web_fallback`
checked, question: "what is the top law cases in Nigeria" against the
`phase1-demo` matter) surfaced two real issues. Both are traced to the actual
code below, not guessed at — per [CLAUDE.md](../CLAUDE.md), this is guidance
for *you* to apply, nothing here has been changed in the repo.

## 1. Why the web-search checkbox didn't fire

### What was actually found
The fallback trigger in
[chain.py](../src/policy_advisor/generation/chain.py):

```python
if not retrieved:
    if not allow_web_fallback:
        ...
    web_result = search_official_sources(...)
```

This only runs `search_official_sources` when the corpus retriever returns
**zero** chunks. Look at
[hybrid_retriever.py](../src/policy_advisor/retrieval/hybrid_retriever.py)'s
`retrieve()`: there is no relevance-score floor anywhere in it. It normalizes
and fuses whatever BM25 and vector search return and hands back the top `k`
(8, per `RETRIEVAL_TOP_K`) — *however weak the match*. For a matter that has
any indexed documents at all, `retrieve()` essentially never returns an empty
list; it returns the 8 *least bad* chunks even for a question the corpus has
nothing to do with.

That's exactly what happened in the test: "top law cases in Nigeria" pulled
back 8 chunks of Civil Procedure Rules and the Meta v NDPC judgment because
those are the only documents in that matter — not because they're relevant.
`retrieved` was non-empty, so the `if not retrieved:` branch (and the web
fallback inside it) never ran. Claude then correctly refused to answer from
irrelevant context, per the faithfulness-first prompt design — the checkbox
was never consulted at all. This is a **relevance-threshold gap**, not a
missing tool integration.

### Separately: it's not "search Google," on purpose
Even when the fallback does fire, `search_official_sources` in
[web_search.py](../src/policy_advisor/generation/web_search.py) uses Claude's
native `web_search_20250305` server tool with `allowed_domains` locked to:

```python
OFFICIAL_SOURCE_DOMAINS = ["nass.gov.ng", "ndpc.gov.ng"]
```

Per that file's own docstring, this is deliberate: an open web result has no
provenance the lawyer vetted, so it's kept out of the faithfulness-checked
corpus pipeline and visually flagged as lower-trust in `app.py` (the
"⚠️ From an official source on the web" bordered container). A general "let
the agent Google anything" tool is a real, well-documented liability for a
legal product — LLMs fabricating case citations from unvetted search results
is the same failure mode behind actual sanctions cases against lawyers who
filed AI-hallucinated citations. "A lawyer should be able to ask any
question" and "every citation this tool produces must be traceable to a
vetted source" are in direct tension; this design resolves it by restricting
scope rather than restricting the question.

### Options, in order of what to do first
1. **Add a relevance floor to `HybridRetriever.retrieve()`** — e.g., drop
   candidates below a minimum `fused_score`, or check whether the *top*
   result clears a threshold before returning anything. This is the actual
   fix for what you saw: it lets "nothing relevant" be represented as
   `retrieved == []` again, so the existing (correctly built) fallback logic
   in `chain.py` finally gets a chance to run. Needs the golden set in
   [03-data-and-retrieval.md](03-data-and-retrieval.md#retrieval-evaluation--do-this-before-trusting-any-answer)
   to pick a threshold empirically rather than guessing a number — too high
   and you'll trigger web fallback on legitimate-but-hard queries, too low
   and this bug persists.
2. **Or, have Claude judge relevance instead of a raw score cutoff** — pass
   the retrieved chunks and question to a small classification-style call
   ("do these passages actually address this question?") before deciding to
   answer from corpus vs. fall back. More robust to score-normalization
   quirks than a fixed float threshold, at the cost of one extra LLM call
   per question.
3. **Expand `OFFICIAL_SOURCE_DOMAINS` deliberately** — this is explicitly
   flagged in the file as a starter draft. Candidates worth a real lawyer's
   review: Supreme Court of Nigeria, Court of Appeal, NJC, FRCN law
   reporting bodies. Treat this as a legal-judgment decision to make with
   someone qualified, not an engineering call to make solo — the docstring
   is right to leave it open rather than guess.
4. **Don't** turn this into an unrestricted web-search tool. If the real
   need is "answer general legal-knowledge questions beyond this matter's
   documents," that's a different, bigger product decision (a separate
   general-knowledge mode, clearly labeled as such) — not a widening of a
   fallback that's currently designed around a curated, low-hallucination
   source list.

## 2. Why it felt like a 3-question limit

### What was actually found
Grepped the whole `src/` tree for anything resembling a quota, budget, or
rate limiter — there isn't one:

- `max_tokens=1024` (`chain.py`, `web_search.py`) and `max_tokens=2048`
  (`case_reasoning.py`, `translate.py`) are each a **per-response output
  cap** — the most tokens Claude is allowed to generate in that single
  reply. None of them are a running total across a session, and there's no
  code anywhere that counts cumulative usage or cuts a user off after N
  calls.
- Chat history isn't silently inflating each call either: `chain.answer()`
  (`chain.py` lines 106–110) builds its prompt from only the current
  question and freshly retrieved context — `st.session_state.messages` in
  `app.py` is for rendering the UI, it's never replayed back into the LLM
  call. So token cost per question stays roughly flat; it doesn't grow with
  conversation length.
- `config.py` has no `max_questions`, `daily_limit`, or similar setting.

So nothing in this codebase is capping you at 3 questions. That points at
the **Anthropic API account itself** — usage tiers carry their own
requests-per-minute / tokens-per-minute / tokens-per-day ceilings that
Anthropic enforces server-side, independent of anything in this repo, and a
low tier or a low prepaid credit balance can both produce exactly this
symptom (fine for a couple of calls, then failures). That's console-side,
not something to grep for here:
- Anthropic Console → **Settings → Limits** — current tier's RPM/TPM/TPD and
  where you're sitting against them.
- Anthropic Console → **Billing** — credit balance / auto-reload status. A
  drained balance fails closed the same way a rate limit does.

### A related gap worth closing regardless
[06-production.md](06-production.md#reliability-extras-worth-planning-for)
already calls this out as unfinished: *"Rate limiting / cost ceilings: a
legal advisor tool with no usage cap is a surprise bill waiting to
happen."* That's still true and still unimplemented — worth building
independent of whatever the console-side cause turns out to be, both for
cost control and so a user sees "you've used today's quota, try again at
midnight" instead of a raw API error.

One more thing this test surfaced: only `chain.py`'s `_call_llm` is wrapped
in the `tenacity` retry/backoff (`stop_after_attempt(3)`,
`wait_exponential`). The raw `client.messages.create` call in
`web_search.py` and the `ChatAnthropic` calls in `case_reasoning.py` and
`translate.py` have no retry at all — a transient 429 on any of those three
paths surfaces immediately as a hard failure instead of backing off and
retrying. If account-level rate limiting turns out to be the actual cause
here, those three call sites are where a rate-limit hit will be felt
hardest, and they're currently the least protected against it.

Next: back to [06-production.md](06-production.md) for the broader
reliability checklist these two findings feed into.
