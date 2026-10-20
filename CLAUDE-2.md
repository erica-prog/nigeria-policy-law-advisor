# CLAUDE-2.md

Phase 2 project memory: **Advanced RAG Policy Advisor**. Extends the Phase 1 system
(fixed 3-document Nigerian civil procedure corpus, described in `CLAUDE.md`
and built out in `docs/00-08`) into a general-purpose legal document advisor.
Read `CLAUDE.md` first - the response rules, audience, and production
philosophy there still apply; this file only outlines what's new.

## Goal
Generalize the advisor from a fixed corpus to three new capabilities:
1. **Bring-your-own-document** - a lawyer can add any legal document
   (statute, contract, judgment, pleading) at runtime and ask questions
   grounded in it, without a code change or re-deployment.
2. **Case outcome reasoning** - given a legal case (facts + applicable
   authorities), the advisor produces a reasoned assessment of the likely or
   best-supported outcome, not just a lookup of existing text.
3. **Multi-language support** - detect a document's language, and let the
   lawyer choose their conversational language independently of it, with the
   advisor translating between the two.

These are different problems wearing the same "RAG" label: (1) is an
ingestion/scaling problem; (2) and (3) are both reasoning/synthesis problems
with real liability exposure if framed wrong - a mistranslated legal term of
art is no safer than a fabricated citation. Treat them as three workstreams,
not one feature.

## New capability 1: arbitrary document ingestion

Outline of what changes from Phase 1's fixed `CORPUS` list
(`policy_advisor/ingestion/chunk.py`):

- **Upload path**: a lawyer adds a document (PDF, possibly DOCX) through the
  UI rather than it being a hardcoded filename. Ingestion becomes an
  on-demand job, not a one-time batch step.
- **Document-type detection**: Phase 1 hand-classified each file as
  statute/judgment. At scale this must be inferred - structural heuristics
  (numbered Order/Rule patterns vs. narrative paragraph numbering vs.
  contract clause numbering) or a cheap classification pass before choosing
  a chunking strategy. Wrong classification = wrong chunker = bad citations.
- **Decided: per-matter/session corpus, not a shared index.** An uploaded
  document is only visible within its own matter, given client
  confidentiality. Needs a `matter_id`/`session_id` on every chunk's
  metadata and a retrieval filter to match, not a separate vector store per
  session (operationally unmanageable at any scale).
- **Index lifecycle**: Phase 1's `build_index` rebuilds everything from
  scratch each run (intentionally, for a fixed small corpus). That doesn't
  hold once documents are added incrementally by many users - need an
  incremental upsert path (add this document's chunks without touching
  everyone else's) and a deletion path (a lawyer removes a document; their
  matter's index should reflect that immediately, including BM25, not just
  the vector store).
- **New failure modes to design for**: malformed/corrupted uploads, documents
  in unsupported formats, documents too large for synchronous ingestion
  (needs an async job + status, not a blocking request), and documents in
  languages/jurisdictions outside the embedding model's strength.
- **Retrieval scoping**: a question should default to searching the
  documents relevant to the current matter, not the entire multi-tenant
  corpus - jurisdiction filtering from Phase 1 generalizes into a broader
  "scope" filter (matter, client, document set).

Planned doc: `docs/09-document-ingestion.md`.

## New capability 2: case outcome / best-judgment reasoning

This is the higher-risk addition and needs to be scoped carefully before any
code gets written.

- **Reframe the ask before designing the prompt.** "What's the best
  judgement" is ambiguous between (a) "what outcome is this court likely to
  reach" (a prediction) and (b) "what's the strongest-supported position
  given the authorities" (an analysis). Build (b). Predicting a specific
  court's decision is a different, much harder, and more liability-laden
  claim than assessing argument strength against retrieved authority - and
  it's not what a research aid should claim to do. This distinction needs to
  be explicit in the system prompt, not left implicit.
- **Inputs**: case facts (lawyer-provided, free text or structured), the
  document(s)/authorities already in the matter's corpus, and possibly a
  retrieval pass over case law for analogous precedent (extends Phase 1's
  `search_case_law`-style retrieval from a single judgment to a case-law
  collection).
- **Output shape, not just prose**: structured reasoning is both more useful
  and more auditable than a single verdict-style answer -
  issues-identified -> arguments for each side -> authorities supporting
  each -> an assessed strength/weakness per issue -> an overall reasoned
  position. Forces the model to show its work, and gives the lawyer
  something to disagree with at a specific step rather than a black-box
  conclusion.
- **Every claim still traces to a retrieved authority - decided: tiered
  per-step checking, not final-only.** The cheap deterministic
  citation-existence check (`generation/faithfulness.py`) runs on every
  step's output, since it's effectively free and final-only checking misses
  errors that get paraphrased away by the time the chain reaches synthesis -
  the entire point of decomposing the answer into steps is that each piece
  is independently verifiable. The more expensive Claude-as-judge check
  (catches unsupported inference beyond bad citations) runs at step
  boundaries - where one step's output is about to be treated as a settled
  premise by the next - plus exhaustively on the final synthesis. Each
  step's check validates against that step's own retrieved/cited authority
  set, not the whole chain's pooled retrieval from the start, since later
  steps (e.g. the analogous-precedent search) retrieve new authorities
  mid-chain. A failed check triggers one corrective retry of that step, then
  - if still unresolved - the step is flagged low-confidence in the
  structured output rather than letting an unsupported premise flow forward
  unflagged. Checking without a defined failure action is an audit log, not
  protection.
- **Calibration and hedging are first-class requirements, not polish.** The
  output must distinguish "strongly supported by directly on-point
  authority" from "plausible but no close precedent found in this corpus."
  An RAG agent that sounds equally confident either way is the single
  biggest risk in this feature.
- **Disclaimers belong in the output, every time this capability is used**,
  not just once in a UI footer - e.g. explicitly state this is not a
  prediction of any court's actual decision and does not substitute for the
  lawyer's judgment or independent research. This is a liability boundary,
  not boilerplate.
- **Evaluation is harder than Phase 1's golden set.** Recall/precision/MRR
  evaluate retrieval; they don't evaluate whether a reasoned legal argument
  is sound. Likely needs a human-reviewed rubric (e.g. "did it identify the
  correct issues," "did it cite the strongest available authority for each
  side," "did it avoid overstating confidence") scored by someone with legal
  judgment, not just an automated metric.

Planned doc: `docs/10-case-outcome-reasoning.md`.

## New capability 3: multi-language support & translation

Carries the same order of risk as capability 2: a mistranslated legal term
of art is as dangerous as a fabricated citation, and deserves the same
"don't overstate confidence" discipline, not a bolt-on UI feature.

- **Decided: document translation, not conversation-only translation** - the
  source text itself is translated during ingestion and stored as the
  long-term corpus asset, since this is a persistent system rather than a
  one-off per-conversation convenience. This is the riskier of the two
  options ("cite the exact locator" no longer means "the quoted text is the
  literal original wording") so it comes with non-negotiable mitigations:
  - **Retain the original-language text alongside the translation in chunk
    metadata.** Never store translation-only. The lawyer (and any human
    reviewer) must be able to check a translated citation against the
    literal original wording, not trust the translation as the source of
    truth.
  - **Translation happens once, at ingestion, not per-query.** Treat it like
    embedding: a versioned build step, re-run (and re-checked) only when the
    source document or the translation model/prompt changes - not
    regenerated on every question.
  - **The faithfulness chain now has two axes, not one** (see capability 2's
    resolved per-step granularity): does the citation locator exist in
    what was retrieved, and does the translated text actually say what the
    original said. Both run at the same per-step granularity once this feeds
    case-outcome reasoning.
- **Document language detection**: identify the source document's language
  during ingestion (cheap language-ID pass, not the embedding model itself)
  and store it as chunk metadata - needed before any translation decision
  can be applied per-document, and before choosing an embedding model (see
  next point).
- **Embedding model implications, and which text gets embedded.** Phase 1's
  `BAAI/bge-small-en-v1.5` is English-only. Two separate decisions now that
  documents are translated and stored in both languages: (1) swap to a
  multilingual embedding model (e.g. BGE-M3 or multilingual-E5) so retrieval
  works across both the original and translated text rather than needing
  per-language models - re-run the golden set after any swap, since it may
  trade off some English-retrieval quality against Phase 1's numbers; (2)
  embed the lawyer's working/conversational language's text by default
  (since that's what queries will be phrased in), while still retaining the
  original for citation verification per the point above. Re-embedding is a
  full corpus rebuild either way.
- **Conversational language preference**: ask the lawyer which language they
  want to converse in (not infer it silently) - store it per session/matter,
  surface it visibly in the UI so it's never ambiguous which language a
  given answer was generated in versus which language the underlying
  document is actually written in.
- **Translation fidelity check, run at ingestion (not per-query)**: since the
  translated chunk is now the long-term corpus asset, verify it against its
  retained original at build time - cheap heuristics first (e.g.
  length-ratio sanity checks), Claude-as-judge later for chunks that look
  suspicious, same cost-ordering principle as Phase 1's faithfulness checks.
  This is separate from, and in addition to, the per-step citation-existence
  check that runs at query time during reasoning (capability 2).
- **Scope to start with two languages, not "main languages" generally** -
  English plus one other (French is the natural second given the existing
  corpus's Nigerian/ECOWAS context) - validate the design on two before
  generalizing to N, same reasoning as Phase 1 validating retrieval on three
  documents before assuming the architecture scales.

Planned doc: `docs/11-multi-language-support.md`.

## Cross-cutting production concerns (extends `docs/06-production.md`)

- **Multi-tenancy**: once documents are user-uploaded, access control
  (who can query which matter's documents) becomes a security requirement,
  not a nice-to-have - was implicit/trivial in Phase 1's single shared
  corpus.
- **Cost ceilings**: both new capabilities can be invoked far more often and
  more expensively (case reasoning likely means a longer prompt and possibly
  multiple chained Claude calls) than Phase 1's single-pass Q&A - re-check
  the rate-limiting guidance in `docs/06-production.md` against real usage
  patterns once this ships.
- **Observability extends, not replaces**: log which matter/document scope a
  query ran against, and for case reasoning, log each reasoning step's
  citations separately so a bad conclusion can be traced to the specific
  step that introduced it.

Planned doc: `docs/12-multi-tenancy-and-risk.md`.

## Out of scope (for this phase, unless asked)
- Generating the full implementation - same rule as `CLAUDE.md`.
- Any feature that frames output as predicting a specific court's actual
  decision rather than assessing argument strength against retrieved
  authority - that reframe is a hard requirement, not a future nice-to-have.
- Supporting document formats beyond PDF/DOCX until the core ingestion path
  is validated on those two.
- Supporting more than two languages (English + one other) until the
  document-translation pipeline and its fidelity checks are validated on
  that pair.
- Translation-only storage (discarding the original-language text) - the
  original must always be retained alongside the translation, no exceptions.

## Implementation TODOs

Sequenced so each phase's output is what the next phase needs. Phase A is
foundational (everything else assumes a multi-document, scoped corpus
exists); B and C can run in parallel once A lands. Cross-cutting items
thread through all three rather than landing at the end.

### Resolved architecture decisions
- **Per-matter/session corpus**, not a shared index (capability 1) - gates
  the data model for every task below.
- **Document translation**, not conversation-only (capability 3) - the
  source text is translated and stored as the long-term corpus asset; the
  original is always retained alongside it for citation verification.
- **Tiered faithfulness checking** (capability 2) - the cheap deterministic
  citation-existence check runs on every reasoning step; the expensive
  Claude-as-judge check runs at step boundaries and on the final synthesis;
  a failed check triggers one corrective retry, then a low-confidence flag
  if still unresolved. See capability 2's writeup above for the full
  reasoning.

### Phase A - generalized document ingestion (`docs/09-document-ingestion.md`)
- [ ] Design the chunk/index metadata schema extension: `matter_id`
      (mandatory - every chunk belongs to exactly one matter),
      `document_type` (inferred, not hand-mapped), `language` (original),
      plus a slot for translated text per target language (capability 3).
- [ ] Build document-type classification (structural heuristics first, e.g.
      Order/Rule numbering vs. paragraph numbering vs. contract clauses;
      fall back to a cheap classification pass).
- [ ] Replace Phase 1's fixed `CORPUS` list with an upload path: accept a
      file, run ingestion on-demand, write to the matter-scoped index.
- [ ] Add incremental upsert to the vector store and BM25 index (no full
      rebuild per upload) and a matching deletion path - both scoped to a
      single matter so one upload/deletion never touches another matter's
      index.
- [ ] Add async handling for large uploads (job + status, not a blocking
      request) and validation for malformed/unsupported files.
- [ ] Implement the matter-scope filter in `HybridRetriever` (generalizes
      Phase 1's jurisdiction filter) - a query must default to its own
      matter's documents only, never the full multi-tenant store.
- [ ] Re-run an expanded golden set against multi-document, multi-matter
      retrieval before calling this phase done, including a test that a
      query in matter A never returns a chunk scoped to matter B.

### Phase B - case outcome / best-judgment reasoning (`docs/10-case-outcome-reasoning.md`)
- [ ] Write the reframed system prompt: argument-strength analysis, not
      outcome prediction - explicit, not implied.
- [ ] Design the structured output schema (issues -> arguments per side ->
      supporting authorities -> per-issue assessment -> overall position).
- [ ] Extend retrieval to a case-law collection for analogous precedent
      (builds on Phase A's generalized ingestion).
- [ ] Extend `faithfulness.py` for the resolved tiered approach: the
      deterministic check on every step's output, scoped to that step's own
      retrieved/cited authority set (not the chain's pooled retrieval).
- [ ] Add the Claude-as-judge check at step boundaries and on the final
      synthesis (not on every step - cost/latency only justified at the
      seams where one step's output becomes the next step's premise).
- [ ] Implement the failure-action contract: one corrective retry per failed
      step, then a low-confidence flag surfaced in the structured output if
      still unresolved - a check with no defined action on failure is an
      audit log, not protection.
- [ ] Add calibration/hedging language requirements to the prompt and spot-
      check that confidence is differentiated, not uniform.
- [ ] Add the mandatory per-response disclaimer (not a one-time UI footer).
- [ ] Build the human-reviewed evaluation rubric (issue identification,
      strongest-authority selection, confidence calibration) - this phase
      isn't done when retrieval metrics look good, unlike Phase 1.

### Phase C - multi-language support (`docs/11-multi-language-support.md`)
- [ ] Add a language-ID pass to ingestion; store language as chunk metadata.
- [ ] Build the document-translation step in the ingestion pipeline: runs
      once at build time (versioned, like embedding), writes the translation
      into chunk metadata alongside the retained original - never replacing
      it.
- [ ] Build the translation-fidelity check at ingestion time (length-ratio
      heuristic first, Claude-as-judge for flagged chunks) - this gates what
      enters the long-term corpus, separate from the per-step citation check
      that runs later at query time.
- [ ] Evaluate a multilingual embedding model candidate (e.g. BGE-M3) against
      Phase 1's golden set in English before adding a second language, to
      catch any English-retrieval regression from the swap; decide which
      language's text gets embedded by default (the lawyer's working
      language) versus retained only for citation verification (the
      original).
- [ ] Add the conversational-language preference prompt/setting, stored per
      session/matter, surfaced visibly in the UI.
- [ ] Validate end-to-end on English + one second language before treating
      "main languages" generally as in scope - include a test that a
      translated citation's fidelity check actually catches an injected
      mistranslation, not just that the pipeline runs.

### Cross-cutting, threaded through A/B/C (`docs/12-multi-tenancy-and-risk.md`)
- [ ] Add access control so a query can only retrieve its own matter's
      documents (becomes a real security requirement once uploads exist).
- [ ] Re-check rate-limiting/cost-ceiling guidance against the more
      expensive call patterns in B and C (longer prompts, chained calls).
- [ ] Extend structured logging with matter/document scope per query, and
      per-reasoning-step citations for capability 2.
