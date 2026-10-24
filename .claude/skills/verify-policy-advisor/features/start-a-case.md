# Start a new case

Start a new case lets a signed-in person open a blank conversation. The case row is stored when the server creates the matter, which the page does on the first send. The `+ Start a new case` button only clears the conversation on screen.

## Sub-features

- `case-button` resets the on-screen conversation.
- `case-create` stores a case owned by the signed-in user.
- `case-read` shows that case again from the case list and from `meta.json`.

## How to get to it (user POV)

- Log in, then choose `Your cases`, then `+ Start a new case`.
- The first message in the composer calls `POST /api/matters` with no id. Sending that message also starts a chat job. Do not send a message in this recipe.

## Driving it with pa-verify

Preconditions:

- `pa-verify doctor` printed `pa-verify doctor ok`.
- Logged in as `verify` with the cookie jar at `.cursor/skills/verify-policy-advisor/run/cookies.txt`.
- `URL` is `http://127.0.0.1:8765` and `DATA` is the `data=` line from doctor.
- `OUT` is `.cursor/skills/verify-policy-advisor/evidence/start-a-case`.
- No case is titled `Foxglove supply dispute`.

- **Create the case.** This is the call `ensureMatter` makes, with a title so the list has a readable name. Run `curl -sS -c "$JAR" -b "$JAR" -o "$OUT/create.json" -w '%{http_code}' -H 'content-type: application/json' -d '{"title":"Foxglove supply dispute"}' "$URL/api/matters"`. The status is `201`. The body has `owner` `verify`, `read_only` `false`, `title` `Foxglove supply dispute`, and `document_count` `0`. Record `id` as `CASE`.
- **Read it back.** Run `curl -sS -b "$JAR" -o "$OUT/one.json" "$URL/api/matters/$CASE"` and `curl -sS -b "$JAR" -o "$OUT/list.json" "$URL/api/matters"`. The one-case body matches the title and owner. The list contains that id.
- **Read the stored file.** Run `cp "$DATA/index/matters/$CASE/meta.json" "$OUT/meta.json"`. The file contains `"owner": "verify"` and `"title": "Foxglove supply dispute"`.

## Gotchas

- Choosing `+ Start a new case` does not create a row. The browser creates the matter on the first send, and that send also calls the model. Prove the create call directly. Do not send a chat message to force it.
- A title is optional in the API. This recipe sets one so the stored file can be checked.
- The shared library is not this case. `phase1-demo` is read-only and is not owned by `verify`.
