# 29 — Playbooks and routing

**Status:** implemented (docs/23 slice P2-F). **Decisions:** ADR-001 action 13 and
the playbook decision; docs/23 D9.

## What a playbook is

A YAML file that says what the advisor does for one practice area in one
country: `src/policy_advisor/playbooks/data/<practice_area>/<country>.yaml`.
Four ship: investigations and immigration, each for Nigeria and the United
States. A playbook has:

| Field | Role |
|---|---|
| `practice_area`, `country`, `jurisdictions` | Taxonomy ids (docs/22); a playbook never applies outside its country. |
| `triggers` | Words and phrases in the lawyer's message that point at it. |
| `mode` | The mode it runs in when the lawyer did not say: `analysis` (needs documents) or `research`. |
| `allowed_tools` | Patterns the research agent's tool policy intersects with the registry. A playbook can only narrow what a lawyer signed off, never widen it. |
| `checklist` | What the agent must work through and report on, item by item. |
| `extraction_schema` | Facts to record when the sources give them. |
| `output_sections` | The headings of the answer. |
| `notice` | The caveat the lawyer sees instead of the generic research notice. |
| `attribution` | Where adapted text came from (the investigations checklists adapt the MIT `legal-ai-skills` plugin's skills). |

The loader validates every field against the taxonomy and refuses a playbook
without triggers, tools, a checklist or a notice. A bad file fails at first use.

## The selector

`decide_mode` from Phase 2 is now `select_route`
(`playbooks/selector.py`): a mode and, when one fits, a playbook.

1. An explicit intent (`analyze`, `ask`, `research`) sets the mode.
2. A playbook matches when its country agrees with the matter's jurisdiction
   (when one is given) and its triggers appear in the message as whole words;
   phrases count double, and a tie goes to the playbook whose jurisdiction list
   names the matter's.
3. With `auto` intent a matched playbook chooses its mode, falling back to
   research without documents and to a question on the shared library.
   Without a playbook the Phase 2 rules apply unchanged.

The selector calls no model. It is the same function in the API and in the
evals, so what the evals pass is what the product does.

## What a playbook changes at run time

- **Research mode.** The agent runs with the playbook's `allowed_tools` as its
  allow-list, the checklist, extraction fields and headings appended to its
  system prompt, and the audit row's mode set to `research:<playbook id>`.
  A chain or web tool outside the allow-list is refused and audited like a
  connector tool.
- **Analysis mode.** The case-reasoning chain runs as before (its prompt is
  fixed); the reply carries the playbook's notice.
- **Every reply** names the playbook in `ChatReply.playbook`, which the client
  shows above the notice.

## The routing evals

`eval/routing/*.yaml` in the plugin's shape: `prompt`, `expected_skill` (a
playbook id or `none`), `expected_behavior` (the mode), `must_include` and
`must_not_include`, plus the route's inputs (`jurisdiction`, `has_documents`,
`intent`, `conversation_started`, `analysed`, `read_only`). The runner checks
the selector's choice and the playbook's notice and checklist. Eighteen cases
across investigations, immigration and the general rules; CI runs them in the
lint-and-test job because they need no key.

```bash
uv run python -m eval.run_routing_evals
```

Writing the cases found three defects before any user could: a missing
trigger ("adjust status"), and two checklist phrasings a reviewer would not
find. That is the loop these evals are for: a routing complaint becomes a case,
the case fails, the playbook changes, the case passes.

## What these evals do not show

Whether the model follows the checklist, ignores an injected instruction, or
keeps evidence and authority apart in its prose. Those need a run against a real
model with graded outputs, which is the Phase 3 evaluation work (bulk analysis
and drafting will need the same harness). The deterministic check on citations
(docs/28) is the part that holds regardless.

## Adding a playbook

Add the practice area to the taxonomy if it is new, write the YAML, add cases to
`eval/routing/`, run the evals, then the tests. A lawyer reviews the checklist
and notice before the playbook is used with clients; the four shipped here have
not had that review.
