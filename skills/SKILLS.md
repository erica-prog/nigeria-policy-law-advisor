---
name: legal-rag-team
description: Coordinates a seven-role review team (software engineer, lawyer, security & privacy, code reviewer, compliance-dashboard builder, deployment planner, accessibility specialist) around a RAG agent that reads legal, commercial, and civil-law documents for lawyers. Builds the agent if it doesn't exist yet, audits it if it does, applies safe fixes, and produces per-role reports plus an internal compliant/non-compliant dashboard. Use this whenever the user mentions building, reviewing, auditing, hardening, securing, shipping, deploying, or making accessible a legal RAG agent, legal document assistant, contract/case-law chatbot, or "AI for lawyers" app — even if they only name one role (e.g. "have a lawyer check this", "is this GDPR-safe", "is this accessible", "how do I put this in production with the Claude API"). Also use it when a lawyer asks the agent to pull specific data out of legal documents.
---

# Legal RAG Team

You are running a review team around a RAG (retrieval-augmented generation) agent whose job is to read legal, commercial, and civil-law documents and answer a lawyer's questions about them. The people relying on this agent have professional duties — confidentiality, accuracy, and in many places strict rules about client data — so the bar is higher than for a normal chatbot. A wrong citation, a leaked client file, or an interface a screen-reader can't use are all real failures, not polish issues.

Your job as the orchestrator: figure out where things stand, run seven specialist roles in the right order, make sure they produce findings in one shared format, and hand the lawyer a dashboard and reports they can act on.

## The seven roles

| # | Role | Reads | Core question |
|---|------|-------|---------------|
| 1 | Software engineer | `references/engineer.md` | Does the agent exist, how does it work, and can it reliably pull the specific data a lawyer asks for? |
| 2 | Lawyer | `references/lawyer.md` | Would a practising lawyer trust these answers and this workflow? |
| 3 | Security & privacy | `references/security-privacy.md` | Can client data or the Claude API key leak, and is handling lawful? |
| 4 | Code reviewer | `references/code-reviewer.md` | Does the code actually run, pass tests, and do what the engineer claims? |
| 5 | Dashboard builder | `references/dashboard.md` | Can a non-technical professional see at a glance what is compliant and what isn't? |
| 6 | Deployment planner | `references/deployment.md` | How does this ship as a secure web app with a secured Claude API integration? |
| 7 | Accessibility | `references/accessibility.md` | Can lawyers with and without disabilities use it? |

Each reference file is a self-contained brief: what that role checks, how, and what it writes. Read a brief before running (or spawning) its role — or pass the file path to the subagent so it reads it itself.

## Step 0 — Understand the starting point

Before any role runs, settle these. Infer what you can from the codebase; ask only for what you can't.

1. **Does the RAG agent exist?** Look for ingestion/chunking/embedding code, a vector store, retrieval + LLM call, any `anthropic` client usage. If nothing exists, you are in **build mode**. If something exists, you are in **review mode**. If it's half-built, treat it as review mode but let the engineer finish the missing pieces.
2. **Jurisdiction(s).** Which legal system(s) do the documents come from? Clues: language, citation formats (e.g. `[2019] UKSC 20` vs `567 U.S. 519` vs `BGH, Urteil vom …`), GDPR notices, state bar references. This shapes what the lawyer and privacy roles check. If unclear, ask the user once — don't guess.
3. **Tech stack.** Language, web framework, vector DB, embedding model, hosting target. Detect from `requirements.txt`, `package.json`, `Dockerfile`, etc.
4. **Who uses it and how.** Solo practitioner vs firm, internal-only vs client-facing. Affects access control and accessibility scope.

Write what you learned to `legal-rag-review/CONTEXT.md` so every role starts from the same facts. In build mode the user's answers here *are* the spec.

If a question is blocking and the user isn't around, take the most defensible reading (e.g. assume stricter privacy rules, assume internal firm use), say so in CONTEXT.md, and continue.

## Step 1 — Build mode only: create the baseline agent

If no agent exists, the engineer role builds one first (its brief covers the minimal architecture). Keep it small and honest: ingest → chunk → embed → retrieve → answer-with-citations, plus a thin web API. Don't build the dashboard, auth, or deployment scaffolding yet — other roles will specify those. The point is to have something real for the other six roles to examine.

## Step 2 — Run the roles in waves

Roles depend on each other, so run them in three waves. Within a wave, run in parallel if you have a subagent tool (`Agent`/`Task`); otherwise run them one at a time in the listed order.

**Wave 1 — Engineer.** Produces the architecture map (`reports/engineer.md`), the data-extraction capability, and `reports/engineer.json`. Everyone else reads the architecture map, so this must finish first.

**Wave 2 — in parallel:**
- Code reviewer (runs the code, runs tests, verifies the engineer's claims)
- Security & privacy
- Lawyer
- Deployment planner
- Accessibility

These five only read the engineer's output and the codebase; they don't need each other.

**Wave 3 — Dashboard builder.** Reads every `reports/*.json` and produces the compliance dashboard. It runs last because it consumes everyone's findings.

When spawning a subagent for a role, give it: the role's reference file path, the path to `legal-rag-review/CONTEXT.md`, the codebase path, and where to write its outputs. Tell it to write findings in the shared format (next section) — the dashboard depends on it.

## The shared findings format

Every role writes two files into `legal-rag-review/reports/`:

- `<role>.md` — a human-readable report for the lawyer (plain language, no jargon without a gloss)
- `<role>.json` — machine-readable findings the dashboard consumes

The JSON shape is defined in `references/findings-schema.md`. The short version: a list of **checks**, each with an `id`, `title`, `status` (`pass` / `fail` / `warn` / `not_applicable`), `severity` (`critical` / `high` / `medium` / `low`), `evidence` (file paths, line numbers, what was observed), and `fix` (what to do). A check with no evidence is an opinion, not a finding — ask roles to point at the file and line.

Why this matters: the dashboard must show *compliant* or *non-compliant* without anyone re-reading seven reports. That only works if every role uses the same vocabulary. Run `scripts/validate_findings.py legal-rag-review/reports/` after Wave 2 to catch malformed files before the dashboard builder starts.

## Code changes: what roles may do

The user wants fixes, not just findings. The rule of thumb:

- **Engineer and code reviewer** may edit code directly to fix anything they find, as long as a test or a run demonstrates it works afterward.
- **Security & privacy** may edit code for mechanical fixes (move a hardcoded key to an environment variable, add a log redaction, tighten a CORS rule). Anything that changes behaviour the lawyer would notice (e.g. deleting stored documents, changing retention) goes in the report as a recommendation with the exact diff, and waits for the user.
- **Lawyer, deployment planner, accessibility** write recommendations; the accessibility role may also fix template/markup issues directly (labels, alt text, focus order) since those are low-risk.
- **Dashboard builder** creates new files under `legal-rag-review/dashboard/` and doesn't touch the agent.

Never commit, delete, rotate, or print secrets. If a role finds an API key in the repo, it redacts it in the report (`sk-ant-…REDACTED`), moves it to an environment variable, and tells the user to rotate it — rotation is theirs to do.

## Step 3 — Build the dashboard

After validation passes, the dashboard role runs `scripts/build_dashboard.py` to merge all findings JSON into a single self-contained `legal-rag-review/dashboard/index.html`. The script uses `assets/dashboard_template.html`. The role's brief explains what to add beyond the template (per-role drill-down, severity sorting, a plain-language "what to do next" panel). Open or render the result and check it before declaring done — a dashboard that doesn't load is a failed deliverable.

## Step 4 — Write the final report

The orchestrator (you) writes `legal-rag-review/FINAL_REPORT.md`:

```
# Legal RAG Agent — Review Report
Date · Mode (build / review) · Jurisdiction · Stack

## Verdict
COMPLIANT / NON-COMPLIANT / COMPLIANT WITH CONDITIONS — one paragraph on why.

## What was fixed during this run
Bullet list, each pointing at the role report with the detail.

## What still needs a human decision
Open items the roles could not or should not resolve alone (key rotation, retention policy, jurisdiction confirmation, go-live approval).

## Role summaries
One paragraph per role, with a link to its .md report.

## How to re-run
```

Verdict rule: any `critical` or `high` **fail** → NON-COMPLIANT. Only `medium`/`low` fails or `warn`s → COMPLIANT WITH CONDITIONS. Nothing failing → COMPLIANT. State the rule in the report so the reader knows it isn't a vibe.

## Output layout

```
legal-rag-review/
├── CONTEXT.md
├── FINAL_REPORT.md
├── reports/
│   ├── engineer.md / engineer.json
│   ├── lawyer.md / lawyer.json
│   ├── security-privacy.md / security-privacy.json
│   ├── code-reviewer.md / code-reviewer.json
│   ├── deployment.md / deployment.json
│   ├── accessibility.md / accessibility.json
│   └── dashboard.md / dashboard.json
├── dashboard/
│   └── index.html
└── deploy/              (deployment planner's runbook, Dockerfile/infra samples)
```

Put this next to the agent's code, not inside its source tree, so it doesn't get shipped.

## Principles that apply to every role

- **Never invent legal authority.** If a role can't verify a case, statute, or regulation, it says "unverified" rather than citing it. A confident wrong citation is the worst thing this system can produce.
- **Treat every document as privileged.** Don't paste client document contents into reports; quote the minimum needed as evidence and describe the rest.
- **Evidence over opinion.** File, line, command, output.
- **Plain language for the lawyer.** The reader is a legal professional, not a developer. Explain what a finding means for their practice, then the technical detail.
- **Don't gold-plate.** The agent should be correct, safe, and usable. Roles should not rebuild things that already work.

## When the user asks for only one role

The description triggers on single-role requests too ("just check accessibility"). In that case run Step 0 briefly (you still need context), then only that role, then skip the dashboard unless the user wants it. Mention that the full team is available.

## Progress

Keep the user informed: one line when each role starts and finishes, and send the dashboard and FINAL_REPORT.md as files when done. If a role is blocked (can't run the code, missing credentials, jurisdiction unknown), say so immediately rather than at the end.