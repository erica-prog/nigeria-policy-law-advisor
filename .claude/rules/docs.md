---
paths:
  - "docs/**"
---
# Design documents

- Numbered `docs/NN-title.md`; add every new document to the map in `docs/00-overview.md`.
- Explain how and why before what; lead with production concerns (retrieval quality,
  evaluation, secrets, errors, observability, reliability).
- When TypeScript appears, explain the unfamiliar parts inside the doc.
- `docs/architecture/` and `docs/contracts/` are owned by `lead-architect` and must match
  the code; fix them in the same change that changes the code.
