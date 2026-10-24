---
paths:
  - "web/**"
---
# Browser client

- Plain HTML, CSS and ES modules, no build step; every request goes through
  `web/js/api.js`; shapes come from `docs/contracts/web-api.md`.
- Trust is rendered from server fields (`avatar_state`, `source`, `faithful`, citation
  `kind`, `notice`), never inferred from answer text.
- The client never holds a key beyond the single PUT that stores it, and never logs
  answer or document text.
- Verify changes by driving the real app with the `verify-policy-advisor` skill.
