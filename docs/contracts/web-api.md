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
`matter_exists` (409), `unsupported_document` (415), `upload_too_large` (413),
`validation_error` (422, adds `details: [{loc, msg}]`), `llm_unavailable` (503),
`internal_error` (500). `message` is written by the server and never contains exception
text, file paths or secrets.

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

- `GET /api/health` -> `{ "status": "ok", "llm_configured": true }`. No auth.
- `GET /api/me` -> `{ "username": "jdoe", "display_name": "Jane Doe" }`.

### Auth

- `POST /api/auth/login` `{ "username": "jdoe", "password": "..." }` ->
  `200 { "username", "display_name" }` and sets the cookie. Wrong or unknown
  credentials -> `401 invalid_credentials` (same response for both).
- `POST /api/auth/logout` -> `204`, clears the cookie.
- Sign-up and password reset are not exposed in v1. Use `scripts/add_user.py`.

### Matters

Matter ids match `^[a-z0-9][a-z0-9-]{1,63}$`.

- `GET /api/matters` -> `{ "matters": [Matter] }` where
  `Matter = { "id", "owner", "read_only", "document_count" }`. Includes the shared
  `phase1-demo` matter with `read_only: true`.
- `POST /api/matters` `{ "id": "smith-v-acme-2026" }` -> `201 Matter`.
  `409 matter_exists` if taken.
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
- `503 llm_unavailable` when no model key is configured or the model call failed.

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
