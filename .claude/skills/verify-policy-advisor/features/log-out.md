# Log out

Log out ends the session and returns to the login card. The person's cases stay in the data directory.

## Sub-features

- `logout-button` ends the session from the header.
- `logout-api` rejects later authenticated calls.
- `logout-keeps-cases` leaves an existing case file in place.

## How to get to it (user POV)

- While logged in, choose `Log out` (`#logout-btn`) in the header.
- The login card is shown again.

## Driving it with pa-verify

Preconditions:

- `pa-verify doctor` printed `pa-verify doctor ok`.
- Logged in as `verify`, and a case file already exists if you are proving `logout-keeps-cases`.
- `URL` is `http://127.0.0.1:8765` and `JAR` is the cookie jar.
- `OUT` is `.claude/skills/verify-policy-advisor/evidence/log-out`.

- **Log out.** Run `curl -sS -c "$JAR" -b "$JAR" -o "$OUT/logout.body" -w '%{http_code}' -X POST "$URL/api/auth/logout"`. The status is `204`.
- **Session is gone.** Run `curl -sS -b "$JAR" -o "$OUT/me.json" -w '%{http_code}' "$URL/api/me"` and `curl -sS -b "$JAR" -o "$OUT/session.json" "$URL/api/session"`. `/api/me` is `401` with code `unauthenticated`. `/api/session` is `{"user": null}`.
- **Case file remains.** If `CASE` was created earlier, `"$DATA/index/matters/$CASE/meta.json"` is still present.

## Gotchas

- Logout returns an empty body. Assert the status, not a JSON object.
- The browser also clears `sessionStorage`. A curl proof does not show that. When you drive the button, the next screen is the login card, not the chat.
