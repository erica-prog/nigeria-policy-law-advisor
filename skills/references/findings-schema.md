# Shared findings format

Every role writes `legal-rag-review/reports/<role>.json`. The dashboard builder reads all of them; `scripts/validate_findings.py` checks them. Stick to this shape exactly — the dashboard and the final verdict rule depend on the field names.

## JSON shape

```json
{
  "role": "security-privacy",
  "role_label": "Security & Privacy",
  "generated_at": "2026-10-19T14:32:00Z",
  "summary": "One or two plain-language sentences a lawyer can read.",
  "checks": [
    {
      "id": "SEC-001",
      "title": "Claude API key is not hardcoded",
      "status": "fail",
      "severity": "critical",
      "category": "secrets",
      "evidence": "app/llm.py:12 contains a literal key beginning sk-ant-… (redacted). No .env or secret manager in use.",
      "fix": "Moved key to ANTHROPIC_API_KEY env var and added .env to .gitignore. User must rotate the exposed key at console.anthropic.com.",
      "fixed_in_this_run": true,
      "needs_human_decision": true
    }
  ]
}
```

## Field rules

| Field | Values | Notes |
|-------|--------|-------|
| `role` | kebab-case slug matching the filename | `engineer`, `lawyer`, `security-privacy`, `code-reviewer`, `deployment`, `accessibility`, `dashboard` |
| `role_label` | display name | shown on the dashboard |
| `status` | `pass` · `fail` · `warn` · `not_applicable` | `warn` = works but risky or incomplete; `not_applicable` = checked and genuinely irrelevant (say why in evidence) |
| `severity` | `critical` · `high` · `medium` · `low` | Severity describes how bad a **fail** would be, so set it even on passing checks — the dashboard uses it to order things |
| `category` | free short tag | e.g. `secrets`, `retention`, `citations`, `auth`, `keyboard`, `tests` |
| `evidence` | string | File paths and line numbers, commands run, output observed. "I believe" is not evidence. |
| `fix` | string | What to do, or what was done. Empty string allowed only on `pass`. |
| `fixed_in_this_run` | bool | True only if the role actually changed code and verified it |
| `needs_human_decision` | bool | True when the fix needs the user's authority (rotate a key, pick a retention period, approve go-live) |

## ID prefixes

Use the role's prefix so IDs stay unique across the whole review:

`ENG-` · `LAW-` · `SEC-` · `REV-` · `DEP-` · `A11Y-` · `DASH-`

## Verdict rule (used by the orchestrator and the dashboard)

- Any check with `status: fail` and severity `critical` or `high` → **NON-COMPLIANT**
- Otherwise, any `fail` (medium/low) or any `warn` → **COMPLIANT WITH CONDITIONS**
- Otherwise → **COMPLIANT**

## Markdown report companion

The `<role>.md` file is for the lawyer. Suggested structure:

```
# <Role label> — Review
**Summary:** same sentence as the JSON summary
## What I checked
## What I found (worst first)
### <check id> <title> — <STATUS>
What this means for your practice · Technical detail · What was done / what to do
## What I changed in the code (if anything)
## Open questions for you
```

Keep client document contents out of the report. Quote the minimum needed to prove a point.