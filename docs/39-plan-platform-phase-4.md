# 39 — Plan: platform Phase 4: owed items, a live model evaluation harness, and the next ranked gaps

**Phase:** implementation plan (decisions accepted 2026-10-28; nothing below is built
yet). Same format as [20](20-plan-claude-layout-and-streamlit-removal.md),
[22](22-plan-platform-phase-0-1.md),
[23](23-plan-platform-phase-2.md) and [30](30-plan-platform-phase-3.md). **Base:**
`platform/phase-3-a` at `a36f49a` (Phases 0 to 3 complete as planned). **Owner:** product
owner. **Date:** 2026-10-25.

## 1. Outcome and scope

Three things. First, close what Phases 2 and 3 left owed: thresholds that were never
measured, reviews a lawyer has not given, live runs never made. Second, build the one
capability every earlier phase deferred: an evaluation harness that runs the system
against a real model with graded outputs, so the behavioural questions (does the model
ignore an injected instruction, does it keep evidence and authority apart, does the
drafting rubric hold on real drafts) stop being "for later". Third, take the next gaps the
architecture review (docs/18) and the Phase 2 and 3 records still rank open: the
corpus and retrieval work that was postponed, the operating gaps a firm will hit first, and
the documentation that still disagrees with the code.

**In scope:** the eight slices below. **Out of scope:** new jurisdictions beyond Nigeria
and the United States; deployment infrastructure beyond stating what the image needs
(AGENTS.md); mobile or native clients.

## 2. Constraints observed

- Every keyed evaluation runs only in the nightly workflow (docs/38). The extraction F1 and
  drafting thresholds, the US golden set's generation run, a live drafting run and a live
  Batch API run have not happened because no development machine holds a server key.
  The repository has the Anthropic secrets; it does not yet have the three connector
  secrets, so the hosted canaries are skipped.
- The taxonomy, the source registry rows, the four playbooks and the NigeriaLII exclusion
  carry `reviewed_by_lawyer: false` or the equivalent note. Every phase since 1 has
  recorded this; none has had the review.
- The evaluation that exists is deterministic (locator checks, routing, extraction
  oracle, rubric fixtures) or measures retrieval. Nothing grades a model's prose. The
  injection test (docs/26, docs/28) proves the wrapper, not the model's behaviour.
- The golden set is still one Nigerian matter plus the US library (docs/21 open item);
  the embedding model is still `bge-small-en-v1.5` with the BGE-M3 candidate unevaluated
  (docs/21); the `contract_clause` locator scheme has no parser, so a lawyer's own
  contract is chunked into `Section N` blocks (docs/33 extracts over those).
- Operating gaps a firm meets on day one: no sign-up or password reset (AGENTS.md
  brief item 1), one tenant per user, templates added by row rather than upload, no
  client screens for bulk jobs, no per-tenant cost ceiling, shared-library writes not
  restricted by the database (docs/31), retention of ledgers and trails undecided
  (docs/37), the two review dialogs never driven in a browser (docs/36).
- Documentation that disagrees with the code (docs/18 F8): `docs/product-brief.md`
  describes a React Native app; `docs/architecture/web-mvp.md` predates tenancy,
  tracing, bulk analysis and artefacts; `docs/17` predates the research agent.
- The Docker image has not been built on this machine (the Docker daemon hangs); the
  Node stage and the connector copy are untested end to end.

## 3. Decisions

| ID | Decision | Status | Alternatives and recommendation |
|---|---|---|---|
| D1 | How the live evaluation harness grades | **Accepted 2026-10-28** | **Accepted (recommended):** a graded-case format in the plugin's eval shape extended with `grader` (`deterministic`, `rubric`, `judge`) and `must_cite`; one runner (`eval/run_live_evals.py`) that drives the real chat and research paths through the API harness, applies the deterministic checks first, then Claude-as-judge with a fixed rubric prompt per case kind, and records per-case verdicts with the model and prompt versions. Alternative: a hosted eval product; adds a dependency and moves the record off the repository. |
| D2 | Which model judges | **Accepted 2026-10-28** | **Accepted (recommended):** the strongest model the deployment allows (`claude-opus-5-5` through `JUDGE_MODEL`), distinct from the model under test, with the judge's prompt and version recorded on every verdict. Alternative: the same model judging itself; cheaper and systematically kinder. |
| D3 | Threshold policy | Routine | A threshold is set from the first three nightly measurements (median), recorded in the phase record with the date, and raised only by a decision recorded the same way; a drop below it fails the nightly job and opens an issue (the canary pattern). |
| D4 | Lawyer review mechanics | **Accepted 2026-10-28** | **Accepted (recommended):** a review packet per subject (taxonomy, registry rows, playbooks, the NigeriaLII reading, the drafting rubric criteria) generated from the data files into one Markdown file under `docs/review/`, signed off by editing `reviewed_by` fields in the data, so the review leaves a diff. Alternative: an in-app review screen; more code for a one-time act. |
| D5 | Account lifecycle | **Accepted 2026-10-28** | **Accepted (recommended):** admin-driven invitations (an admin creates the account and a one-time link; the user sets their password), password reset by the same one-time link mechanism, no public sign-up; one tenant per user stays. Alternative: self-service sign-up with email verification (docs/10 explored it); more surface for a product sold to firms. |
| D6 | Contract clause parser | Routine | `ingestion/parse_contract.py` producing `contract_clause` locators (`Clause 12.3`, `Article 4`), classified by cue (numbered clauses with defined terms), falling back to the generic chunker; the extraction schema's locators then name clauses the lawyer recognises. |
| D7 | Golden set growth and the embedding candidate | Routine | A second Nigerian matter (synthetic commercial dispute with evidence documents) and a firm-tier collection in the golden set; BGE-M3 evaluated with the existing harness against both sets and adopted only if recall and MRR improve on both. |
| D8 | Bulk and template screens | Routine | A bulk panel (start, progress, ceiling, resume, records, findings link to the review queue) and template upload (admin, DOCX with a style block) in the plain client; browser-driven checks through the `verify-policy-advisor` skill. |
| D9 | Cost ceilings per tenant | Routine | A monthly token ceiling per tenant on the ledger; the research agent and bulk jobs refuse to start when the month's spend has reached it; an admin raises it; shown on `/api/usage`. |
| D10 | Documentation reconciliation | Routine | Rewrite `docs/product-brief.md` and `docs/architecture/web-mvp.md` against the code as of this phase; retire or mark superseded docs 07, 10, 12 to 15 where they describe abandoned surfaces; one pass, one commit. |

## 4. Acceptance criteria

| ID | Observable result | Basis |
|---|---|---|
| AC-1 | The nightly workflow has run with keys and the extraction F1, the drafting rubric and the US golden set's faithfulness are measured; the thresholds are set by D3 and recorded with dates; a live Batch API run and a live drafting run are recorded | docs/30 owed items |
| AC-2 | A live evaluation harness runs graded cases against a real model through the API harness: at least 30 cases covering injection resistance (connector result and uploaded document), evidence versus authority, citation discipline, routing with a real reply, and research answers; each verdict records model, judge, prompt versions; CI's nightly job fails below the recorded thresholds | ADR decision 9; D1 to D3 |
| AC-3 | The taxonomy, registry rows, playbooks, the NigeriaLII reading and the rubric criteria carry a lawyer's sign-off in the data, produced through the review packet | D4; owed since Phase 1 |
| AC-4 | An admin can invite a user and a user can reset a password through one-time links; no public sign-up exists; tests cover expiry and single use | D5 |
| AC-5 | A contract upload is chunked by clause with `contract_clause` locators, the extraction records name those clauses, and the extraction eval still scores 1.0 on the oracle | D6 |
| AC-6 | The golden set includes a second Nigerian matter and a firm-tier collection; the embedding candidate's measurement is recorded and the decision taken | D7; docs/21 open items |
| AC-7 | The client has a bulk panel and template upload, both exercised in a browser; the review dialogs from docs/36 are exercised the same way | D8; docs/36 owed |
| AC-8 | A tenant's monthly ceiling stops new agent runs and bulk jobs when reached, visible on `/api/usage`; shared-library writes are refused by the database for any tenant but `shared`; a retention policy for ledgers and trails is decided and recorded | D9; docs/31 and docs/37 owed |
| AC-9 | The product brief and the architecture document describe the system as it is; superseded documents say so; the Docker image builds in CI | D10; docs/18 F8 |

## 5. Slices

| ID | Slice | Needs | Result |
|---|---|---|---|
| P4-A | Owed measurements: add the connector secrets, run the nightly workflow, record the thresholds (D3), run the live drafting and Batch API checks, record them | repository secrets | AC-1 |
| P4-B | Live evaluation harness: case format, runner, judge prompt and model setting, first 30 cases, nightly job with thresholds | P4-A; D1, D2 | AC-2 |
| P4-C | Lawyer review packet and sign-off fields | D4 | AC-3 |
| P4-D | Account lifecycle: invitations and password reset by one-time link | D5 | AC-4 |
| P4-E | Contract clause parser and the extraction locators | D6 | AC-5 |
| P4-F | Golden set growth and the embedding candidate decision | D7 | AC-6 |
| P4-G | Bulk panel, template upload, browser checks of the review dialogs | D8 | AC-7 |
| P4-H | Cost ceilings per tenant, database-side restriction of shared writes, ledger retention policy | D9 | AC-8 |
| P4-I | Documentation reconciliation and the image build in CI | D10 | AC-9 |

Order: P4-A first (it costs nothing to build and unblocks every threshold), P4-B next
(the harness is what makes the rest measurable), then P4-C in parallel with P4-D, then
P4-E and P4-F, then P4-G, P4-H, P4-I. One commit per slice with tests; each updates the
contract documents it touches.

## 6. Risks

| ID | Risk | Assumption | Check |
|---|---|---|---|
| R1 | The judge model agrees with itself and not with a lawyer | the judge's verdicts on a sample are compared with a lawyer's during P4-C, and the rubric prompt is adjusted before thresholds bind | the sample comparison is recorded in the phase record |
| R2 | Live evals cost more than the nightly budget allows | cases are capped per run and the ledger records every run; the harness samples cases when the cap is near | the ledger total per nightly run is reported in the job |
| R3 | Thresholds set from three runs are noisy | the median of three, raised only by recorded decision; a drop opens an issue rather than blocking a merge | the canary pattern from docs/38 |
| R4 | One-time links leak or are reused | links are single-use, expire in 24 hours, are stored hashed, and are sent out of band by the admin (no email sender exists) | tests on expiry, reuse and hashing |
| R5 | The clause parser misreads a contract and the extraction cites the wrong clause | the parser falls back to generic blocks when its cues are weak, and the evidence verifier still requires the quote to be in the cited chunk | the extraction eval on the synthetic set plus a hand check on three real-shaped synthetic contracts |
| R6 | The Docker image fails to build in CI because of the Node stage | the build runs in CI before anything depends on it | a `docker build` job on pull requests |

## 7. Verification

The existing gates (`ruff`, `mypy`, `pytest`, routing evals, extraction oracle, rubric
fixtures, Node tests) on every pull request; the nightly workflow with thresholds once
P4-A has set them; the live harness as a nightly job; browser checks through the
`verify-policy-advisor` skill for every client change; a Docker build job in CI.

## 8. Review needs

A lawyer for P4-C, the judge comparison in R1, and the retention decision in P4-H; a
security review of P4-D (one-time links) and the shared-write restriction in P4-H.

## 9. Readiness and next action

All ten decisions are settled: D1, D2, D4 and D5 were accepted with the recommended
answers on 2026-10-28; D3 and D6 to D10 are routine. The plan is ready within its scope.

Pull request 22 merged Phase 3 into `main` up to P3-G (`bf16790`); P3-H (`a36f49a`) and
this plan are on `platform/phase-3-a` and reach `main` with the next pull request. P4-A
starts once they have merged. It needs three connector secrets added to the repository
(`COURTLISTENER_API_KEY`, `GOVINFO_API_KEY` and `REGULATIONS_GOV_API_KEY`, which the
canary job in `.github/workflows/nightly.yml` reads) and one nightly run with keys; no
code precedes it. Next action: merge the remaining Phase 3 commits, add the secrets, and
let the nightly workflow run once.

## 10. Implementation record

| Slice | Branch | Commit | Verification | Notes |
|---|---|---|---|---|
