# 35 — Artefacts: grounded memos and decks

**Status:** implemented (docs/30 slice P3-E). **Decisions:** ADR-001 decision 7 and action
15; docs/30 D5 (docxtpl and python-pptx from one outline), D6 (the drafting agent) and D7's
first state (`draft`).

## What it is

A lawyer gives an instruction ("memo for the partner on whether a defence filed on day
40 is out of time under the FHC rules, and what to do about it"). The drafting run
searches the shared legal libraries and the firm's own collections, writes an outline in
which every paragraph of law names its sources, and the system turns that outline into a
Word memo and a slide deck with a provenance manifest. The artefact enters the `draft`
state. Nothing is sent or filed.

## Authority only

The drafting run is the research agent (docs/28) with a playbook whose tools exclude the
matter's own documents: `search_authority` over the shared libraries, `search_firm_knowledge`
over the firm's `firm`-tier collections, and the connectors. The lawyer's instruction
carries the facts; paragraphs that restate it are marked `instruction` and carry no
sources. The manifest then checks every cited source: a locator that resolves to a
retrieved chunk of trust kind `authority` or `firm` is verified; a URL a tool recorded is
verified; a locator that resolves only to a matter-tier chunk is **rejected**, and both
renderers replace the paragraph with a marker (AGENTS.md rule 4). Anything else is
unverified and marked in the text.

## Handing the outline in

The run hands its outline in through a `submit_outline` tool whose input schema is the
outline model, passed to the research agent as an extra tool that spends no budget. The
API returns tool input as well-formed JSON; an outline written into the reply is not,
because quotation marks in legal text break it. A rejected outline goes back to the
model with the fields to fix (never their text), and a list sent as a JSON string is
read as the list (`policy_advisor.tool_input`). A reply that ends with a JSON object is
still read as a fallback. When neither yields an outline, the error says why (JSON error
position, or the failing fields) without quoting the reply. The runner allows four turns
beyond the tool budget so a run that spends its budget can still hand in. The first
nightly run (2026-10-28) ended all three drafts without an outline before this change.

## The provenance manifest

One entry per cited source per paragraph, in the plugin's fields: proposition,
pinpoint, instrument, issuing body, identifier, source id, trust kind, version date, access
date, verification, limitations, and a status. The judge (`judge_check`, the same
Claude-as-judge the case analysis uses) runs once per section with the passages its
paragraphs cite; its verdict is recorded per section. A flagged section is reported in
the manifest and the files, never silently rewritten. The manifest is stored with the
artefact and rendered into both files.

## Rendering

- **DOCX through docxtpl.** A Word template with Jinja tags, the packaged default
  (`artefacts/templates/memo.docx`, built by `scripts/build_default_templates.py`) or a
  firm's own uploaded to the `templates` table with a style block (font, colours, footer).
  Teaching note: docxtpl renders a `.docx` as a Jinja template, so a firm's designer keeps
  Word and the developer keeps one context object.
- **PPTX through python-pptx.** Title slide, one slide per section with the paragraphs as
  bullets and the sources in the speaker notes, a closing slide of limitations and
  provenance counts.

Both read the same outline, so the memo and the deck share provenance. Files go to the
blob store under the matter (`<matter>/artefacts/<id>.docx`), and the artefact row keeps
the keys.

## The API (contract: docs/contracts/web-api.md)

`GET /api/templates`; `POST /api/matters/{id}/artefacts` (writable matter, the caller's
key); `GET /api/matters/{id}/artefacts` and `.../{artefact_id}`; `GET
.../{artefact_id}/file?format=docx|pptx`.

## Not done here

- Review transitions beyond `draft` and the client screens (P3-F).
- A generated-document rubric scored in CI (P3-H); the judge per section is the check
  that runs today.
- Template upload through the API; firms add templates by row today.
- A live drafting run against a real model; the tests drive the agent with a scripted
  loop and a fake judge.
