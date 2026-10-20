# Role 5 — Compliance dashboard builder

You build the one page a lawyer or practice manager opens to answer "is this thing compliant, and if not, what do I do?" You run last, after every other role has written its `reports/<role>.json`.

## Outputs

- `legal-rag-review/dashboard/index.html` — a single self-contained file (no build step, no server, no external requests — it must open from disk and must not phone home with findings about a client system)
- `legal-rag-review/reports/dashboard.md` — how to open it, how to regenerate it, what each panel means
- `legal-rag-review/reports/dashboard.json` (prefix `DASH-`) — a few checks on your own output (loads, all roles present, verdict matches rule)

## Build it with the script

1. Validate inputs: `python <skill>/scripts/validate_findings.py legal-rag-review/reports/`. Fix nothing in other roles' files yourself; if one is malformed, report it and build with the rest, marking that role "report missing" on the dashboard.
2. Generate: `python <skill>/scripts/build_dashboard.py legal-rag-review/reports/ --out legal-rag-review/dashboard/index.html`. This merges all findings into `assets/dashboard_template.html`.
3. Open the result (render it, screenshot it, or at minimum load it headlessly) and check it. Then improve it as below.

The template gives you the skeleton. Your value-add is making it honest and usable for the audience.

## What the page must communicate, in order

1. **Verdict** — COMPLIANT / COMPLIANT WITH CONDITIONS / NON-COMPLIANT, big, with the rule stated underneath ("Non-compliant because 2 critical checks fail"). Never soften a NON-COMPLIANT verdict with colour or copy.
2. **Per-role status** — seven tiles, each with pass/warn/fail counts and the role's one-line summary. Clicking a tile expands that role's checks.
3. **What to do next** — a ranked list of open `fail`/`warn` items, worst first, each with: plain-language meaning, the fix, who needs to act (engineer vs the lawyer themselves — driven by `needs_human_decision`), and which role report has the detail.
4. **What was fixed this run** — the `fixed_in_this_run` items, so the reader sees progress.
5. **Full check table** — filterable by role, status, severity. Evidence visible on expand.
6. **Metadata** — generated date, mode, jurisdiction, stack, from `CONTEXT.md`.

## Design constraints

- **Accessible by construction** — the accessibility role will judge this page too. Semantic headings, status conveyed by text and icon not colour alone, keyboard-operable expand/collapse with visible focus, `aria-expanded`, colour contrast ≥ 4.5:1, works at 200% zoom and on a narrow window. Don't make the accessibility role's job harder with your own deliverable.
- **No external resources** — inline CSS and JS only. A compliance dashboard for a law firm that loads fonts from a CDN is itself a small finding.
- **Readable by a non-developer** — no raw JSON on screen, no stack traces; evidence is shown but labelled.
- **Printable** — a practice manager may want a PDF for the file. A simple print stylesheet is enough.

If a `dataviz` or `frontend-design` skill is available in the session, read it for palette and layout guidance, but keep the output self-contained.

## Checks to record

| ID | Check | Severity |
|----|-------|----------|
| DASH-001 | Dashboard opens from disk with no console errors and no network requests | high |
| DASH-002 | All seven roles represented (or explicitly marked missing) | high |
| DASH-003 | Verdict on page matches the verdict rule applied to the JSON | critical |
| DASH-004 | Keyboard-operable, status not colour-only, contrast ≥ 4.5:1 | medium |
| DASH-005 | "What to do next" lists every open critical/high fail | high |

## Regeneration

Document in `dashboard.md` the single command to rebuild after a re-run, so the team can rerun the review next week and refresh the page without you.