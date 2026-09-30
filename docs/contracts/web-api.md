# Contract: web API v1

Base path `/api`. All bodies are JSON unless stated. Authentication is the `pa_session`
HttpOnly cookie set by login. Changes to this file go through the lead architect
(`AGENTS.md` rule 9). The FastAPI app also serves the OpenAPI document at `/docs`.

## Error format

Every non-2xx response has exactly this shape:

```json
{ "error": { "code": "matter_not_found", "message": "Matter not found." } }
```

Codes: `unauthenticated` (401), `invalid_credentials` (401), `matter_not_found` (404),
`document_not_found` (404), `not_found` (404), `matter_read_only` (403),
`claude_key_required` (403), `matter_exists` (409), `unsupported_document` (415),
`upload_too_large` (413), `validation_error` (422, adds `details: [{loc, msg}]`),
`invalid_api_key` (400), `rate_limited` (429), `anthropic_unreachable` (502),
`llm_unavailable` (503), `server_not_configured` (503), `internal_error` (500).
`message` is written by the server and never contains exception text, file paths or
secrets; in particular no response, log line or error ever contains an API key.

## Source reference (citation) schema

```json
{
  "id": "c1",
  "kind": "evidence",
  "document": "supply-agreement.docx",
  "locator": "Section 4",
  "cited_as": "Section 4(2)",
  "page": 1,
  "quote": "verbatim text of the retrieved passage",
  "url": null,
  "doc_type": "generic",
  "jurisdiction": null
}
```

- `kind`: `evidence` (uploaded case material: proves facts), `authority` (statutes,
  rules, judgments: supports legal propositions), `web` (official web source). In this
  MVP `kind` is derived from the chunk's `doc_type`: `statute` and `judgment` are
  `authority`; every other uploaded document is `evidence`; web results are `web`.
- `document`: source file name, or page title for `web`.
- `locator`: the chunk locator as stored (`Order 5 Rule 3`, `Paragraph 11`,
  `Section 2`); `null` for `web`. `cited_as` is the string the model wrote, which may be
  a pinpoint inside `locator`.
- `page`: integer page (paragraph-block proxy for DOCX) or `null`.
- `quote`: the retrieved passage verbatim. Always present for `evidence` and
  `authority`; `null` for `web` (the fallback records URLs, not spans).
- `url`: only for `web`.

The server emits a citation only when the locator resolves to a chunk that was retrieved
for that request. Unresolved locators appear in `unsupported_citations` and set
`faithful: false`.

## Endpoints

### Health and identity

- `GET /api/health` -> `{ "status": "ok", "llm_configured": true, "shared_key_allowed": false }`.
  No auth. `llm_configured` is the server-level view (is a key in `.env` at all);
  `shared_key_allowed` mirrors `ALLOW_SHARED_ANTHROPIC_KEY`. Whether a particular user
  can get answers is on `/api/me`.
- `GET /api/me` -> `User = { "username": "jdoe", "display_name": "Jane Doe",
  "advisor_ready": true, "key_source": "user" | "shared" | null }`. `advisor_ready` is
  true when a key can be resolved for this user (revision 3); `key_source` says whose.
- `GET /api/session` -> `{ "user": User | null }`. Always `200`, with or without a valid
  cookie, so the client can bootstrap without a 401 in the browser console. No auth.

### Auth

- `POST /api/auth/login` `{ "username": "jdoe", "password": "..." }` ->
  `200 User` and sets the cookie. Wrong or unknown
  credentials -> `401 invalid_credentials` (same response for both).
- `POST /api/auth/logout` -> `204`, clears the cookie.
- Sign-up and password reset are not exposed in v1. Use `scripts/add_user.py`.

### Claude key (revision 3, bring your own key)

Each user pays Anthropic for their own questions. The key is stored encrypted at rest
on the server and is never returned, logged or echoed in an error; only its last four
characters are ever shown.

- `GET /api/me/claude-key` -> `ClaudeKey = { "configured": false, "last4": null,
  "source": "user" | "shared" | null, "advisor_ready": false }`. `configured` means this
  user has a key on file; `source` is whose key the advisor would use right now
  (`shared` only when the server allows its own key to be shared and the user has none).
- `PUT /api/me/claude-key` `{ "api_key": "sk-ant-..." }` -> `200 ClaudeKey`. The server
  checks the format (starts with `sk-ant-`, no whitespace, plausible length), then
  verifies the key with the cheapest real Anthropic call (`models.list(limit=1)`) before
  storing it. `400 invalid_api_key` when the format is wrong or Anthropic rejects it,
  `502 anthropic_unreachable` when Anthropic could not be reached (the key is not
  stored), `429 rate_limited` after 5 attempts per user per minute,
  `503 server_not_configured` if `AUTH_COOKIE_KEY` is still the insecure default (keys
  are encrypted with a key derived from it, so the server refuses to store anything).
- `DELETE /api/me/claude-key` -> `204`, idempotent.

Ask, analyze and chat use, in this order: the user's own key; the server's `.env` key
if `ALLOW_SHARED_ANTHROPIC_KEY=true`; otherwise they fail with `403 claude_key_required`
(a permission problem for this user, not a server outage, hence not 503). Uploads and
history keep working without a key; ingestion-time translation uses the resolved key
and is skipped when there is none.

### Matters

Matter ids match `^[a-z0-9][a-z0-9-]{1,63}$`. Since revision 2 the browser client never
shows an id; it shows `title`.

- `GET /api/matters` -> `{ "matters": [Matter] }` where
  `Matter = { "id", "owner", "read_only", "document_count", "title", "created_at",
  "updated_at" }`. `title` is `null` until the first chat message derives one (or the
  caller set it at creation); `created_at`/`updated_at` are ISO-8601 UTC strings or
  `null` for legacy matters. Ordered most recently used first. Includes the shared
  `phase1-demo` matter with `read_only: true` and the fixed title
  `"Reference library (shared, read-only)"`, always last.
- `POST /api/matters` `{ "id"?: "smith-v-acme-2026", "title"?: "Late invoices" }` ->
  `201 Matter`. Both fields optional (revision 2): without `id` the server generates
  `case-<12 hex>`; without `title` the first chat message sets it. `409 matter_exists`
  if an explicit id is taken. `title` is 1-80 characters.
- `GET /api/matters/{matter_id}` -> `Matter`. `404 matter_not_found` unless owned or
  shared.

### Documents

`Document = { "name", "status": "queued"|"processing"|"ready"|"failed", "job_id",
"chunk_count", "error" }`. `error` is a curated message, only for `failed`.

- `GET /api/matters/{id}/documents` -> `{ "documents": [Document] }`.
- `POST /api/matters/{id}/documents` multipart `file` (`.pdf` or `.docx`, up to
  `MAX_UPLOAD_MB`), optional form field `jurisdiction` (`federal`|`lagos`) ->
  `202 Document` with status `queued`. Shared matter -> `403 matter_read_only`.
- `GET /api/matters/{id}/documents/{name}` -> `Document` (poll for status).
- `DELETE /api/matters/{id}/documents/{name}` -> `204`.

### Ask

`POST /api/matters/{id}/ask`

```json
{ "question": "When must the defence be filed?", "allow_web": false,
  "jurisdiction": null, "language": "en" }
```

Response `200`:

```json
{
  "answer": "... [Source: Order 5 Rule 3] ...",
  "source": "corpus",
  "faithful": true,
  "unsupported_citations": [],
  "citations": [SourceReference],
  "retrieved": [SourceReference],
  "avatar_state": "verified_source",
  "usage": { "input_tokens": 0, "output_tokens": 0 }
}
```

- `source`: `corpus` (answered from this matter's documents and citation-checked),
  `web` (official-source fallback; only when `allow_web` is true and the corpus had
  nothing), `none` (nothing relevant found).
- `citations`: resolved `[Source: ...]` tags in order of first appearance. For `web`
  answers these have `kind: "web"`.
- `retrieved`: every passage shown to the model, so the client can list "passages
  consulted". Empty for `web` and `none`.
- `403 claude_key_required` when no key can be resolved for this user (see *Claude
  key*); `503 llm_unavailable` when the model call itself failed.

### Analyze

`POST /api/matters/{id}/analyze` `{ "case_facts": "...", "jurisdiction": null,
"language": "en" }` -> `200`:

```json
{
  "issues": [{
    "issue": "Whether the notice was validly served",
    "arguments": [{ "side": "claimant", "summary": "...",
                    "authorities": [{ "locator": "Order 7 Rule 2", "relevance": "...",
                                      "source": SourceReference | null }] }],
    "assessment": "...",
    "confidence": "strongly supported",
    "unverified": false
  }],
  "overall_position": "...",
  "disclaimer": "...",
  "missing_evidence": [{ "issue": "...", "note": "No passage in this matter's documents was retrieved for this issue." }],
  "contradictions": [],
  "counterarguments": [],
  "citations": [SourceReference],
  "avatar_state": "verified_source"
}
```

`missing_evidence` lists issues for which nothing in the matter was retrieved
(`confidence == "no authority found in corpus"`). `contradictions` and
`counterarguments` are reserved and always empty in v1: the prototype's reasoning chain
does not produce them yet, and the API will not invent them. Each side's `arguments`
inside an issue are the closest existing output. `authorities[].source` is `null` when
the locator did not resolve; such issues are already `unverified: true`.

### Chat (revision 2)

The chat-first client sends every message here; the server decides what to run. Ask and
analyze above remain available and unchanged.

`POST /api/matters/{id}/chat`

```json
{ "message": "Acme stopped paying our invoices in March...", "allow_web": false,
  "intent": "auto", "jurisdiction": null, "language": "en" }
```

- `intent`: `auto` (default: the server decides), `analyze` (the user pressed the
  "Analyse my case" chip), `ask` (force a question).
- Routing, in this order:
  1. Analysis is wanted when `intent == "analyze"`, or when `intent == "auto"` and this
     is the first user message of a conversation that has no analysis yet (never for the
     shared read-only library, whose first message is a question).
  2. Analysis wanted **and the matter has documents** -> `mode: "analysis"` via the
     analyze pipeline with `case_facts = message`.
  3. Analysis wanted **and the matter has no documents** -> `mode: "research"`: the ask
     pipeline with `allow_web` forced to `true`, labelled as general legal research.
     `notice` is set and no `evidence` citation can appear (there are no documents).
  4. Otherwise `mode: "question"`: the ask pipeline with the caller's `allow_web`.

Response `200 ChatReply`:

```json
{
  "id": "5f1c...", "role": "assistant", "created_at": "2026-09-29T22:10:00+00:00",
  "mode": "analysis" | "research" | "question",
  "bubble": "I have analysed your case: 2 issues, 1 without support in your documents. The details are below.",
  "notice": null | "This is general legal research from official web sources and the model, not evidence from your documents. ...",
  "avatar_state": "verified_source",
  "citations": [SourceReference],
  "answer": AskResponse | null,
  "analysis": AnalyzeResponse | null,
  "chips": [{ "label": "Ask a follow-up question", "action": "ask" }]
}
```

- Exactly one of `answer` (question, research) and `analysis` (analysis) is set;
  `citations` and `avatar_state` mirror it. Citations use the source-reference schema
  above with the same `kind` labels.
- `bubble` is one short sentence for the speech bubble; `notice` is a caveat the client
  must show next to the reply (`research` always; `question` when `source == "web"`).
- `chips[].action` is one of `analyze`, `add_documents`, `ask_web`, `ask`. The client
  decides what each does (run analysis, open the file picker, resend with `allow_web`,
  focus the composer). Labels are display text only.
- Errors as for ask/analyze: `404 matter_not_found` (ownership is checked before any
  document listing or retrieval), `403 claude_key_required`, `503 llm_unavailable`,
  `422 validation_error`.

Side effects for owned matters: the user message and the reply are appended to the
matter's conversation; `updated_at` is set; if the matter has no `title` and this is
the first user message, `title` becomes the first ~60 characters of the message, cut at a
word boundary with `…`. Nothing is stored for the shared read-only library.

`GET /api/matters/{id}/chat` -> `ChatHistory`:

```json
{ "title": "Acme stopped paying our invoices in March and terminated the…",
  "has_analysis": true,
  "messages": [ { "id", "role": "user", "text", "created_at" }, ChatReply, ... ] }
```

Empty `messages` for the shared library.

### Chat jobs (revision 3, live progress)

`POST .../chat` stays synchronous. The browser client uses the job form instead so the
speech bubble can narrate what the advisor is doing. Same request body, same routing,
same side effects.

- `POST /api/matters/{id}/chat/jobs` (body as for chat) -> `202 ChatJob` with
  `status: "queued"`. The key check (`403 claude_key_required`) happens before the job
  is created.
- `GET /api/matters/{id}/chat/jobs/{job_id}` -> `200 ChatJob`. The job must belong to
  this matter **and** to the logged-in user; anything else is `404 not_found` (a job id
  is never confirmed to exist for someone else, and the shared library's jobs are still
  per user). Finished jobs are kept for 15 minutes.

```json
{ "job_id": "8f0e...", "status": "queued" | "running" | "done" | "failed",
  "stage": "reading_documents" | "searching_web" | "checking_citations" | "writing" | null,
  "stages": ["reading_documents", "writing", "checking_citations", "writing"],
  "reply": ChatReply | null,
  "error": { "code": "llm_unavailable", "message": "..." } | null }
```

- `stage` is the current stage while `running`; `stages` is the sequence so far, in
  order, emitted by the pipeline where the work actually happens (retrieval, the web
  fallback, the model call, the faithfulness/judge checks). Analysis emits them per
  issue, so the same stage may repeat.
- Exactly one of `reply` (`done`) and `error` (`failed`) is set. `error` uses the error
  codes above; internal failures are `internal_error` with a generic message.
- Clients poll about every 700 ms.

## Avatar state

`avatar_state` is one of `idle`, `listening`, `verified_source`, `web_source`,
`unverified`, `no_results`, computed server-side:

| Condition                                    | `avatar_state`    |
| -------------------------------------------- | ----------------- |
| `source == "none"`                            | `no_results`      |
| `source == "web"`                             | `web_source`      |
| `source == "corpus"` and `faithful == false`  | `unverified`      |
| `source == "corpus"` and `faithful == true`   | `verified_source` |
| analysis: no issue has retrieved support      | `no_results`      |
| analysis: any issue `unverified`              | `unverified`      |
| analysis: otherwise                           | `verified_source` |

`listening` is set by the client while a request is in flight; `idle` is the resting and
error state. The client shows a caption per state and keeps it in sync with the frame.

## Versioning

Unversioned path for the MVP. Additive changes (new optional fields) need no version
bump; removing or renaming a field requires `/api/v2` and a note here.

Revision 2 (chat-first client) was additive only: `Matter.title/created_at/updated_at`,
optional `MatterCreate.id/title`, and the `chat` endpoints. No field was removed or
renamed.

Revision 3 (bring-your-own key, live progress) is additive as well: `User.advisor_ready`
and `key_source`, `Health.shared_key_allowed`, `GET /api/session`, the
`/api/me/claude-key` endpoints, and the `chat/jobs` endpoints. One **behaviour** change
is deliberate and flagged: ask, analyze and chat now answer `403 claude_key_required`
instead of `503 llm_unavailable` when the caller has no usable key; `503` remains the
code for a failed model call.
