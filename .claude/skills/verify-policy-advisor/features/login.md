# Log in

Log in lets a person with an account open the advisor. A wrong password stays on the login card and does not start a session.

## Sub-features

- `login-open` shows the login card when nobody is signed in.
- `login-fail` rejects a wrong password and an unknown username with the same error.
- `login-ok` starts a session and shows the person's display name.

## How to get to it (user POV)

- Open `http://127.0.0.1:8765/`. The card title is `Hi, I'm your advisor` and the button is `Let's go`.
- Submit `#login-form`. The username field is named `Username` and the password field is named `Password`.

## Driving it with pa-verify

Preconditions:

- `pa-verify doctor` printed `pa-verify doctor ok`.
- `URL` is `http://127.0.0.1:8765`.
- `JAR` is `.cursor/skills/verify-policy-advisor/run/cookies.txt`.
- `OUT` is `.cursor/skills/verify-policy-advisor/evidence/login`.

- **Open the card.** Run `curl -sS -o "$OUT/page.html" -w '%{http_code}' "$URL/"`. The status is `200` and the file contains `id="login-form"` and `Let's go`.
- **Wrong password.** Run `curl -sS -o "$OUT/wrong.json" -w '%{http_code}' -H 'content-type: application/json' -d '{"username":"verify","password":"wrong-password"}' "$URL/api/auth/login"`. The status is `401` and the body code is `invalid_credentials`. The browser shows `That username or password is not right. Try again.` in `#login-error`.
- **Log in.** Run `curl -sS -c "$JAR" -b "$JAR" -D "$OUT/login.headers" -o "$OUT/login.json" -w '%{http_code}' -H 'content-type: application/json' -d '{"username":"verify","password":"verify-pass-123"}' "$URL/api/auth/login"`. The status is `200`. The body username is `verify`, the display name is `Verify User`, and `advisor_ready` is `false`. The headers include `set-cookie` for `pa_session` and `HttpOnly`.
- **Confirm the session.** Run `curl -sS -b "$JAR" -o "$OUT/me.json" -w '%{http_code}' "$URL/api/me"` and `curl -sS -b "$JAR" -o "$OUT/session.json" "$URL/api/session"`. `/api/me` is `200` with username `verify`. `/api/session` has `user.username` `verify`. In the browser the header shows `Verify User` and a `Log out` button.

## Gotchas

- The UI sentence for a bad password is not the API message. Assert the API code `invalid_credentials` and, in the browser, the sentence in `#login-error`.
- `/api/session` returns `200` with `{"user": null}` when logged out. `/api/me` returns `401`. Do not treat a 200 from `/api/session` as logged in.
- The seeded password exists only in the disposable data directory. Do not write it into `evidence/`.
