# Your cases

Your cases lists the signed-in person's cases and the shared reference library. The shared library is visible to every account and cannot be deleted.

## Sub-features

- `cases-open` shows the drawer.
- `cases-owned` lists a case this account created.
- `cases-shared` lists `Reference library (shared, read-only)`.

## How to get to it (user POV)

- While logged in, choose `Your cases` (`#drawer-toggle`).
- The drawer heading is `Your cases`. Close is the button named `Close`.

## Driving it with pa-verify

Preconditions:

- `pa-verify doctor` printed `pa-verify doctor ok`.
- Logged in as `verify`.
- `URL` is `http://127.0.0.1:8765`.
- `OUT` is `.cursor/skills/verify-policy-advisor/evidence/your-cases`.

- **List cases.** Run `curl -sS -b "$JAR" -o "$OUT/list.json" -w '%{http_code}' "$URL/api/matters"`. The status is `200`. The body includes a matter with `id` `phase1-demo`, `read_only` `true`, and `title` `Reference library (shared, read-only)`.
- **Shared case is not writable.** Run `curl -sS -b "$JAR" -o "$OUT/delete-shared.json" -w '%{http_code}' -X DELETE "$URL/api/matters/phase1-demo"`. The status is `403` and the code is `matter_read_only`.

## Gotchas

- `phase1-demo` is listed even when the disposable data directory has no demo index. The list entry is not proof that demo documents exist.
- Owned cases sort before the shared library. Do not assert a fixed list length if a previous recipe created a case.
