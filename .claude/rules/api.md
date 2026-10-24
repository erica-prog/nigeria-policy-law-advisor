---
paths:
  - "src/policy_advisor/api/**"
---
# Web API

- Identity is resolved from the session cookie on every `/api/*` request except login
  and health; matter ownership (`get_matter` in `deps.py`) runs before any handler
  touches documents, retrieval or generation. Unknown or foreign matters return 404.
- One error shape (`docs/contracts/web-api.md`): a stable `code` and a message we wrote.
  Never forward an exception message or Anthropic's error text to the client.
- No secret, key value, file path or stack trace in a response or a log line. User
  Claude keys: decrypt in memory for the request, pass `api_key=` explicitly down the
  chain, never cache a chain across users.
- Citations are resolved against retrieved chunks before they become `SourceReference`s;
  `kind` is `authority`, `evidence` or `web` and the client renders trust from it.
- Contract changes go through `lead-architect` and `docs/contracts/` first.
