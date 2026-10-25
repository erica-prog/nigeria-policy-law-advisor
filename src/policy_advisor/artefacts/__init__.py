"""Artefacts: memos and decks drafted from the firm and authority tiers,
with a provenance manifest and a review state (ADR-001 decision 7; docs/30
slice P3-E; docs/35).

- `outline.py`: the one outline model both renderers consume.
- `provenance.py`: every paragraph to the passages that support it; a
  paragraph that leans on case evidence is rejected.
- `render.py`: DOCX through docxtpl, PPTX through python-pptx.
- `store.py`: the `templates` and `artefacts` tables.
- `draft.py`: the drafting run on the research agent, authority-only.
"""
