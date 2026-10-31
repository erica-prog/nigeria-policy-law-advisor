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
| D3 | Threshold policy | Routine; **amended 2026-10-29** | A threshold is set from the first three nightly measurements, recorded in the phase record with the date, and raised only by a decision recorded the same way; a drop below it fails the nightly job and opens an issue (the canary pattern). **Amendment (accepted 2026-10-29):** the threshold is the median less 0.03, not the median itself. A gate at the median fails about half of ordinary nights: the three drafting means were 0.890, 0.881 and 0.885, while one draft alone moved by up to 0.09 between runs. 0.03 is about one of three drafts dropping by 0.1. Drafting: 0.855. Extraction: after its third run on version 2. |
| D4 | Lawyer review mechanics | **Accepted 2026-10-28** | **Accepted (recommended):** a review packet per subject (taxonomy, registry rows, playbooks, the NigeriaLII reading, the drafting rubric criteria) generated from the data files into one Markdown file under `docs/review/`, signed off by editing `reviewed_by` fields in the data, so the review leaves a diff. Alternative: an in-app review screen; more code for a one-time act. |
| D5 | Account lifecycle | **Accepted 2026-10-28** | **Accepted (recommended):** admin-driven invitations (an admin creates the account and a one-time link; the user sets their password), password reset by the same one-time link mechanism, no public sign-up; one tenant per user stays. Alternative: self-service sign-up with email verification (docs/10 explored it); more surface for a product sold to firms. |
| D6 | Contract clause parser | Routine | `ingestion/parse_contract.py` producing `contract_clause` locators (`Clause 12.3`, `Article 4`), classified by cue (numbered clauses with defined terms), falling back to the generic chunker; the extraction schema's locators then name clauses the lawyer recognises. |
| D7 | Golden set growth and the embedding candidate | Routine | A second Nigerian matter (synthetic commercial dispute with evidence documents) and a firm-tier collection in the golden set; BGE-M3 evaluated with the existing harness against both sets and adopted only if recall and MRR improve on both. |
| D8 | Bulk and template screens | Routine | A bulk panel (start, progress, ceiling, resume, records, findings link to the review queue) and template upload (admin, DOCX with a style block) in the plain client; browser-driven checks through the `verify-policy-advisor` skill. |
| D9 | Cost ceilings per tenant | Routine | A monthly token ceiling per tenant on the ledger; the research agent and bulk jobs refuse to start when the month's spend has reached it; an admin raises it; shown on `/api/usage`. |
| D10 | Documentation reconciliation | Routine | Rewrite `docs/product-brief.md` and `docs/architecture/web-mvp.md` against the code as of this phase; retire or mark superseded docs 07, 10, 12 to 15 where they describe abandoned surfaces; one pass, one commit. |
| D11 | What the bulk extractor escalates | **Accepted 2026-10-28: measure first. Decided 2026-10-29: (a)** | **Outcome:** the first pair (run 40809993154, §9a), rescored with the governing-law fix, gave escalated 0.944 and triage only 0.949 mean F1: escalation added −0.004, under 0.02, so (a). Implemented in `9c3e766`, which also removed the triage-only step. **Amended 2026-10-29 (accepted):** a complete agreement that reports no risk clause is flagged and re-read by the escalation model, the one miss (a) would otherwise hide (fourth nightly run, §9a). **Accepted (recommended):** the nightly `extraction-f1` job runs the same 12 contracts twice, once escalated and once with `--no-escalation` (no record read twice). It uploads both reports. The first such pair on version 2 of the labels decides. If escalation adds less than 0.02 mean F1, take (a); otherwise take (b). The triage-only step is removed in the commit that implements the choice. Twelve contracts make one pair a noisy measure; a gain close to 0.02 is worth a second pair before choosing. Options: (a) escalate on the confidence floor only; risk clauses flag the record for the lawyer but do not trigger a second read. Cheapest. (b) re-read only the flagged risk clauses with the escalation model and merge them into the triage record; this matches docs/30 D3 as accepted. Moderate code in `bulk/extract.py`. (c) keep the whole-document re-read; about three times the triage cost for an unmeasured gain. Background: the second nightly run (§9a) showed the implementation re-reads the *whole document* with the escalation model whenever the triage record reports any risk clause. Docs/30 D3 accepted escalating *flagged clauses*. Almost every contract has a risk clause, so all 12 were escalated and about two thirds of the 127,000 tokens went on the second read. The confidence floor never fired: confidence measures whether the evidence quotes are verbatim, and it was 1.0 on every record. |

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

## 9a. The nightly runs

### The first nightly run (2026-10-28)

Run 40765323626 on the merge of pull request 23 (`5587486`). Every job reported success;
two of the three measurements P4-A needed were empty because of defects, not model
quality, so no threshold is set from this run.

| Job | Measured |
|---|---|
| Nigerian golden set | recall@8 1.0, MRR@8 0.875, faithfulness 11 of 12 (g11 unfaithful), correct refusals 10 of 10 |
| US golden set | recall@8 1.0, MRR@8 0.759 on 14 scored questions; generation skipped, so no faithfulness |
| Relevance-floor calibration | margin +0.0007 between the worst positive and the best negative; the two-band configuration stays |
| Case-reasoning invariants | 3 seed cases, 0 failing |
| Extraction F1 (12 contracts) | not measured: one field sent as a JSON string failed the whole job (fixed, `bb9aedd`) |
| Drafting rubric (3 drafts) | not measured: no draft ended with a valid outline (fixed, `3524c61`) |
| Connector canaries | `legal-research` and `ng-sources` pass; CourtListener and GovInfo skipped for missing secrets |
| Cost ledger | no rows from the golden-set, case-reasoning, calibration or canary runners: no tenant set (fixed, `062a9e1`) |

Still owed for P4-A: a nightly run on these fixes, the two thresholds from it (D3: the
median of three runs), the CourtListener and GovInfo secrets, a US faithfulness
measurement (the US step runs with `--skip-generation`), and a live Batch API run, which
the nightly job does not exercise (it extracts twelve contracts on the sync path).

### The second nightly run (2026-10-28)

Run 40781057006 on the merge of pull request 24 (`4057014`). Every job reported success,
and all three fixes from the first run held. The extraction job finished `done` with no
skipped documents. All three drafts produced an outline, including one written after its
tool budget was spent. No `cost_ledger_no_tenant` line appeared in any job.

| Job | Measured |
|---|---|
| Nigerian golden set | identical to the first run: recall@8 1.0, MRR@8 0.875, faithfulness 11 of 12, refusals 10 of 10. g11 was unfaithful again, so it is consistent, not noise |
| US golden set | identical to the first run: recall@8 1.0, MRR@8 0.759; generation still skipped |
| Relevance-floor calibration | identical: margin +0.0007 |
| Case-reasoning invariants | 3 seed cases, 0 failing |
| Extraction F1 (12 contracts) | mean F1 0.699 under version 1 of the labels: parties 0.792, governing law 1.000, key dates 0.261, obligations 0.529, obligation evidence 1.000, defined terms 0.500, risk clauses 0.590, classification 0.917. 127,000 tokens; all 12 escalated (D11) |
| Drafting rubric (3 drafts) | mean 0.890: ng-late-defence-memo 0.943, us-adjustment-eligibility-memo 0.871 (the judge flagged 3 sections), ng-consent-judgment-slides 0.857 |
| Connector canaries | unchanged: two pass, CourtListener and GovInfo skipped for missing secrets |

**The extraction figure is not a baseline.** Most of the lost precision came from the
answer key, not the model. Version 1 labelled one defined term per contract (Business
Day). The schema asks for every term a deadline depends on, and every contract also
defines the Effective Date and the Term; the model returned 36 terms for 12 labelled. The
key also left out the termination and governing-law clauses, both kinds the schema lists
and both in every contract: 43 risk clauses returned for 18 labelled. It left out the
duties that risk clauses impose ("shall indemnify", "shall not carry on any business
competing"). Key dates were matched on the exact wording of their label, so "Termination
notice" did not match "Notice period" for the same "ninety days". Version 2 of the set
(this branch) labels all of these from the schema's own descriptions and matches dates
and deadlines on the words as written. The contract text is unchanged. Version 1's 0.699
is therefore not counted toward the D3 median. The extraction count starts at the next
run, which uploads the predicted records (`extraction-last-run` artifact) so a scorer
question can be settled by rescoring.

**The drafting figure counts as the first of three**, with one known defect. The rubric
checks every case against the memo headings (Question, Short answer, Authority, Analysis,
Next steps). `instructions.yaml` says each case names its expected sections, but none
does and the runner ignores the field. The slides case scored 0 on structure for that
reason alone. The memos scored 0.6, two of five headings missing; the next run uploads
the headings (`drafting-last-run` artifact) to show which. Which sections a client deck
must have is a question for the lawyer review (D4 lists the drafting rubric criteria).

### The third nightly run (2026-10-29)

Run 40809993154 on the merge of pull request 25 (`ee05d44`). Every job reported success.
This is the first run on version 2 of the extraction labels and the first D11 pair. All
three artifacts (`extraction-last-run`, `extraction-last-run-triage`, `drafting-last-run`)
were read.

| Job | Measured |
|---|---|
| Nigerian golden set | recall@8 1.0, MRR@8 0.875, refusals 10 of 10; faithfulness 10 of 12: g11 unfaithful for the third time, g09 for the first |
| US golden set | unchanged: recall@8 1.0, MRR@8 0.759; generation still skipped |
| Relevance-floor calibration | unchanged: margin +0.0007 |
| Case-reasoning invariants | 3 seed cases, 0 failing |
| Extraction F1, escalated (12 contracts) | mean F1 0.944; 127,533 tokens; all 12 re-read by `claude-sonnet-4-6` |
| Extraction F1, triage only | logged 0.876, rescored 0.949 (below); 69,669 tokens; `claude-haiku-5-5` only |
| Drafting rubric (3 drafts) | mean 0.881: ng-late-defence-memo 0.890, us-adjustment-eligibility-memo 0.895, ng-consent-judgment-slides 0.857 |
| Connector canaries | unchanged |

Per field, escalated against triage only (F1, after the governing-law fix):

| Field | Escalated | Triage only |
|---|---|---|
| parties | 0.792 | 1.000 |
| governing law | 1.000 | 1.000 (0.417 as logged) |
| key dates | 0.967 | 0.989 |
| obligations | 0.831 | 0.943 |
| obligation evidence | 1.000 | 1.000 |
| defined terms | 1.000 | 0.935 |
| risk clauses | 0.966 | 0.889 |
| classification | 1.000 | 0.833 |

**Governing law was a scorer defect.** The triage model returned the whole clause ("This
Agreement is governed by the laws of England and Wales.") or the bare law ("England and
Wales") for 7 of 12 contracts. The schema asks for the law "as written"; all 12 answers
were right, and exact matching scored 7 of them wrong. Governing law now agrees on
whole-word containment, the rule already used for dates and deadlines (`5517c20`). Only
that field changes, so the rescore is exact from the logged counts.

**D11 is decided: (a).** Rescored, escalation adds −0.004 mean F1 for about 1.8 times the
tokens, far from the 0.02 line, so no second pair is needed. Even if every party role the
escalation model hedged ("Supplier / Principal") were accepted, the gain would be 0.012.
The extractor now re-reads only records below the confidence floor; risk clauses flag a
record for review and no longer buy a second read (`9c3e766`). Neither read is better
everywhere. The triage model missed more risk clauses (recall 0.857: no risk clause at all
for `distribution-005`, so that record was not even flagged) and classified two complete
agreements as an excerpt and "other". The escalation model named party roles by category
(Supplier and Buyer for a Provider and Client) and returned ten obligations the key does
not hold, against two for the triage model. Option (b) would not have caught the `distribution-005` miss either, since it
re-reads only clauses the triage record reports.

**Counts toward D3.** Extraction: the triage-only 0.949 is the first of three, because it
is the configuration that now ships. Drafting: 0.881 is the second of three.

**The memo headings are a drafting defect, not a rubric one.** The drafting playbook's
`output_sections` reach the system prompt as "Structure the answer under these headings:
Question; Short answer; Authority; Analysis; Next steps." The memos used topic headings
instead ("Governing Rules: Time for Appearance and Filing of Defence", "Option A: ...").
The Nigerian memo lacks Authority, Analysis and Next steps; the US memo lacks Short answer
and Authority. A likely cause is that the instruction speaks of "the answer", while the
outline instruction in the user turn (`OUTLINE_INSTRUCTIONS`) asks for sections without
naming them, and the answer is now a one-line reply after `submit_outline`. The deck's
headings shared a word with one of the five memo headings; its expected sections are still for the lawyer review.

### The fourth nightly run (2026-10-29)

Run 40815006421 on the merge of pull request 26 (`935f4ba`). Every job reported success.
This is the first run with D11 (a) and the named memo sections. Both artifacts were read.

| Job | Measured |
|---|---|
| Nigerian golden set | recall@8 1.0, MRR@8 0.875, refusals 10 of 10; faithfulness 10 of 12: g11 unfaithful for the fourth time, g05 for the first (g09 passed) |
| US golden set | unchanged: recall@8 1.0, MRR@8 0.759; generation still skipped |
| Relevance-floor calibration | unchanged: margin +0.0007 |
| Case-reasoning invariants | 3 seed cases, 0 failing |
| Extraction F1 (12 contracts, triage only) | mean F1 0.915; 68,802 tokens; none re-read; one contract skipped (below) |
| Drafting rubric (3 drafts) | mean 0.885: ng-late-defence-memo 0.856, us-adjustment-eligibility-memo 1.000, ng-consent-judgment-slides 0.800 |
| Connector canaries | unchanged |

**The memo sections held.** Both memos used Question, Short answer, Authority, one or more
"Analysis: ..." sections and Next steps, and scored 1.0 on structure (0.4 and 0.6 before).
The Nigerian memo lost instead on the judge, which flagged 5 of its 8 sections, and on
grounding: 3 paragraphs had unverified or missing sources. The deck scored 0 on structure,
as expected while no deck sections are named.

**One contract was dropped for one missing field.** The triage model left `obligee` out of
one obligation in `distribution-011`. Validation rejected the whole record, so the contract
was skipped and scored as all misses: 3 risk clauses, 2 parties, 1 governing law and the
rest. A lawyer would see the contract listed as not extracted. Nothing retries a record
that fails validation, under (a) or before it.

**`distribution-005` returned no risk clauses in two runs running, so it was not flagged.**
The contract has termination, data-protection, indemnity and governing-law clauses. The
record shows the model read them: its gaps discuss Clauses 5, 6, 7 and 9, its obligations
include the indemnity and the data-protection duty, and its governing law is right. Only the
`risk_clauses` list came back empty. Under (a) nothing re-reads it, and with no risk clause
and full confidence the record is not flagged, so a lawyer would see a contract that
appears to carry no risk. Risk-clause recall was 0.762 (0.82 without the skipped contract).

**Counts toward D3.** Extraction: 0.915 is the second of three (0.949, 0.915). Drafting:
0.885 is the third of three (0.890, 0.881, 0.885), so its median is 0.885. The first two
drafting runs had the headings defect and this one did not; the means stayed close because
the memo that gained on structure lost on the judge.

### The fifth nightly run (2026-10-29)

Run 40820503356 on the merge of pull request 27 (`e3b7ff4`). Every job reported success,
and the drafting gate (0.855) passed on its first night. Both artifacts were read.

| Job | Measured |
|---|---|
| Nigerian golden set | recall@8 1.0, MRR@8 0.875, refusals 10 of 10; faithfulness 11 of 12: g11 unfaithful for the fifth time |
| US golden set | unchanged: recall@8 1.0, MRR@8 0.759; generation still skipped |
| Relevance-floor calibration | unchanged: margin +0.0007 |
| Case-reasoning invariants | 3 seed cases, 0 failing |
| Extraction F1 (12 contracts, triage only) | mean F1 0.938; 69,198 tokens; all 12 extracted; no retry and no re-read |
| Drafting rubric (3 drafts) | mean 0.927: ng-late-defence-memo 1.000, us-adjustment-eligibility-memo 0.952 (the judge flagged 3 sections), ng-consent-judgment-slides 0.829 |
| Connector canaries | unchanged |

**Neither new extraction path fired.** Every record validated on the first read, so the
retry was not needed, and `distribution-005` reported its four risk clauses on the first
read, so the re-read was not needed either. Both paths are proven by tests, not yet by a
live run. One weakness shows in the records: the re-read applies only to a contract
classified as complete, and the triage model classified `distribution-005` as "other" this
time and `lease-006` as an excerpt in three runs out of three. A misclassified contract
with no risk clauses would still escape the re-read. Classification F1 was 0.833 in all
three runs on version 2.

**Extraction threshold set: 0.908.** The three runs on version 2 are 0.949, 0.915 and
0.938; the median is 0.938, and amended D3 sets the gate 0.03 below it. The nightly
`extraction-f1` job now fails below 0.908 and opens or comments on an "Extraction F1 below
threshold" issue.

Still owed for P4-A:

- the deck's expected sections;
- classification: the triage model calls complete agreements an excerpt or "other" (F1
  0.833 in all three runs). The no-risk re-read no longer asks for "complete"; it skips
  only an excerpt or a schedule (`platform/phase-4-a-6`);
- what a party's `role` holds: the schema's examples (Buyer, Supplier, Licensor) invite a
  category, the key holds the defined term, and neither model filled `defined_as`;
- `BULK_ESCALATION_MODEL` is blank, so escalation falls back to `ANTHROPIC_MODEL`
  (`claude-sonnet-4-6`), older than the triage model. It fires only below the confidence
  floor or for a complete agreement with no risk clause, neither of which has occurred on
  a live run since D11 was decided;
- g11, unfaithful in all five runs: found and fixed on `platform/phase-4-a-6`. The answer
  cited `[Source: Paragraph 13; Paragraph 13 (Part 2)]`, both parts retrieved, and the
  deterministic check read the bracket as one locator that matched nothing; the same
  shape (`Paragraph 12; Paragraph 12 (Part 2)`) explains g09 and g05 on their one bad
  night each. The check, the API's citation resolver and the client now split a bracket
  on `;`, and the prompt asks for one locator per bracket. Confirm on the next nightly
  run that faithfulness is 12 of 12;
- the CourtListener and GovInfo secrets;
- a US faithfulness measurement: the nightly US step runs generation from
  `platform/phase-4-a-6`; read the first figure from the next run;
- a live Batch API run.

A smaller defect: `research_finished` logs `citations: 0` for every drafting run, because
the drafts now hand in their sources through `submit_outline` rather than the final
answer. The artefact manifest (`manifest_verified`) is the figure to read.

## 10. Implementation record

| Slice | Branch | Commit | Verification | Notes |
|---|---|---|---|---|
| P4-A (part) | `platform/phase-4-a` | `062a9e1` cost ledger tenant for eval runners | `pytest` green; the two new tests fail with the controls removed | API and worker paths were not affected |
| P4-A (part) | `platform/phase-4-a` | `bb9aedd` per-document extraction failures, JSON-string lists, failed parses counted | `pytest` green; oracle gate 1.0 on 48 contracts; new tests fail without the fix | contract note on `error` for a `done` job |
| P4-A (part) | `platform/phase-4-a` | `3524c61` drafting hands in its outline through `submit_outline` | `pytest` green; new tests fail without the fix; the second nightly run drafted all three cases | one draft was handed in after its tool budget was spent, the path this fix added |
| P4-A (part) | `platform/phase-4-a-2` | extraction set version 2, matcher on dates and deadlines as written, records and headings uploaded | `pytest` green; oracle gate 1.0 on 48 contracts; the two new tests fail on version 1 and on the old matcher | contract text unchanged; first run's 0.699 not counted toward D3 |
| P4-A (part) | `platform/phase-4-a-3` | `5517c20` governing law matched on the law named | `pytest` green; the new test fails on the old scorer | rescores the third run's triage-only 0.876 to 0.949 |
| P4-A (part) | `platform/phase-4-a-3` | `9c3e766` D11 (a): re-read only below the confidence floor; nightly triage-only step removed | `pytest` green; the sync job test fails on the previous code | risk clauses still flag a record for review |
| P4-A (part) | `platform/phase-4-a-3` | memo sections named in the drafting prompt (`drafting_prompt`) | `pytest` green; the new test fails for memo and both on the previous prompt | decks unchanged until the lawyer review; the next nightly run confirms |
| P4-A (part) | `platform/phase-4-a-4` | `5f0af58` drafting gated at 0.855; D3 amended to the median less 0.03; fourth run recorded | workflow YAML parses; the gate uses the runner's existing `--fail-below` | a drop opens or comments on the "Drafting rubric below threshold" issue |
| P4-A (part) | `platform/phase-4-a-4` | `7bbce86` one corrective retry for a record that fails validation, sync and batch | `pytest` green; the new test fails with the retry removed | the batch path now charges an invalid item to the ledger |
| P4-A (part) | `platform/phase-4-a-4` | a complete agreement with no risk clause is flagged and re-read (D11 amendment) | `pytest` green; the new test fails with the check disabled | excerpts and schedules are left alone |
| P4-A (part) | `platform/phase-4-a-5` | extraction gated at 0.908 (D3: median 0.938 less 0.03); fifth run recorded | workflow YAML parses; the gate uses the runner's existing `--fail-below` | a drop opens or comments on the "Extraction F1 below threshold" issue |
