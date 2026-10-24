---
paths:
  - "tests/**"
---
# Tests

- Fixtures are synthetic or anonymised; nothing may resemble a real client document.
- `tests/conftest.py` refuses non-local database hosts and exports a placeholder
  Anthropic key; tests that need a real key end in `_real_call` and are skipped without one.
- Name new tests after the behaviour or the acceptance criterion they prove; when a test
  guards a regression, show once that it fails when the control is removed.
