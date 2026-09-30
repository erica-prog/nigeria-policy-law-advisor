# Architecture: web MVP (FastAPI backend + browser client)

Status: accepted for the MVP. Owner: lead architect. This is the first end-to-end web
slice of the product described in `AGENTS.md` and `docs/product-brief.md`. It wraps the
existing Python prototype in a FastAPI backend and adds a small, dependency-free browser
client with the advisor character from `docs/15-avatar-character-asset.md`. The React
Native app, managed auth provider, PostgreSQL + pgvector and object storage from the
long-term brief are deliberately out of scope here; nothing below prevents them.

## Components

```
Browser (web/, plain HTML + CSS + ES modules)
   |  HTTPS, JSON, HttpOnly session cookie, multipart upload
   v
FastAPI app  src/policy_advisor/api/          <- trust boundary: all secrets live here
   |- auth: credentials.yaml (bcrypt) + signed cookie (itsdangerous)
   |- permissions: get_matter() dependency (owner check before anything else)
   |- routers: auth, session, matters, documents, ask, analyze, chat, chat jobs,
   |           claude-key, health
   |- chat flow: routers/chat.py decides analysis / research / question (revision 2)
   |- conversations: per-matter conversation.json log + title derivation
   |- user_keys: per-user Claude keys, Fernet-encrypted at rest (revision 3)
   |- services: one shared HybridRetriever; RAGChain / CaseReasoningChain built per
   |            API key (small LRU keyed by key fingerprint)
   |- jobs: in-process document-processing table (queued/processing/ready/failed)
   |- chat_jobs: in-process chat job table with progress stages (revision 3)
   |- static: web/ at "/", assets/avatar at "/avatar"
   v
Existing prototype modules (reused, not rewritten)
   ingestion/ingest_document.py   add_document, remove_document
   ingestion/matter_store.py      owner mapping (+ title/timestamps), list_documents,
                                  list_matters_for_user
   retrieval/hybrid_retriever.py  HybridRetriever (used only to resolve cited locators)
   generation/chain.py            RAGChain.answer
   generation/case_reasoning.py   CaseReasoningChain.analyze
   generation/web_search.py       reached only through RAGChain(allow_web_fallback=True)
   v
Storage (unchanged from main): Chroma under data/index/chroma, per-matter chunks,
meta.json (owner, title, created_at, updated_at) and conversation.json under
data/index/matters/<matter_id>/, credentials and encrypted user keys
(user_keys.json) under data/auth/.
```

The API layer never opens Chroma or the chunk files itself. Everything goes through the
public functions listed above, so the storage PR (#4, Supabase Postgres + pgvector) can
replace the bottom layer without changing the API.

## Trust boundary

- The browser holds only a signed, HttpOnly, SameSite=Lax session cookie. It never sees
  `ANTHROPIC_API_KEY`, `AUTH_COOKIE_KEY`, file paths or stack traces (rule 2).
- **User Claude keys are user secrets and get the same care** (revision 3). A key
  travels browser -> server exactly once, in the `PUT /api/me/claude-key` body over
  HTTPS. The server verifies it against Anthropic, then stores it in
  `data/auth/user_keys.json` encrypted with Fernet under a key derived from
  `AUTH_COOKIE_KEY` (HKDF-SHA256, fixed info string), file mode `0600`, written under a
  `filelock` lock with an atomic replace. It is decrypted only in memory, per request,
  to build that user's chain. No endpoint returns it (only `last4`), no log line
  contains it (the store logs `claude_key_stored` with the last four characters), and
  Anthropic's error text is never forwarded. The server refuses to store keys while
  `AUTH_COOKIE_KEY` is the insecure default. Tests assert the plaintext is absent from
  the file, from every response body and from captured logs.
- Key resolution (`user_keys.resolve_anthropic_key`) is: the user's own key; else the
  server key only if `ALLOW_SHARED_ANTHROPIC_KEY=true`; else none, and generation
  endpoints answer `403 claude_key_required`. The server key therefore never leaves the
  process in either mode, and one user's key can never serve another: chains are built
  per key and the `api_key` is passed explicitly down to `RAGChain`,
  `CaseReasoningChain`, `search_official_sources` and ingestion translation.
- All model, search and storage calls happen in the FastAPI process.
- Error responses use one JSON shape (`docs/contracts/web-api.md`) with a stable `code`
  and a human message written by us. Unhandled exceptions are logged with
  `logging_utils` and returned as `internal_error` with no exception text.
- Uploaded files are written to a per-job temporary directory, ingested, then deleted.
  Filenames are reduced to a safe basename; matter ids are validated against a strict
  slug pattern so they can never form a path outside `data/index/matters/`.
- Document text and web text reach the model only through the existing prompt templates,
  which already wrap retrieved passages as context (rule 6). The API adds no new prompt.

## Identity and permissions (rule 1)

1. `POST /api/auth/login` verifies the username and password against the same
   `data/auth/credentials.yaml` that `scripts/add_user.py` and the Streamlit login write
   (bcrypt hashes, the format `streamlit_authenticator` uses). On success the server
   sets a cookie containing `{"u": username}` signed with `AUTH_COOKIE_KEY`
   (`itsdangerous.URLSafeTimedSerializer`, 7-day max age, matching the prototype).
2. `current_user` dependency: reads and verifies the cookie on every `/api/*` request
   except login and health. Missing or invalid cookie -> `401 unauthenticated`.
3. `get_matter` dependency: runs before any handler that touches documents, retrieval or
   generation. It resolves ownership from `matter_store.get_matter_owner`:
   - owner == current user -> full access;
   - `phase1-demo` (the shared reference matter from `config.PHASE1_DEMO_MATTER_ID`) ->
     read-only access for every logged-in user, as in the prototype;
   - anything else, including matters that do not exist -> `404 matter_not_found`.
     404 rather than 403 so the API does not confirm that another user's matter exists.
4. Only after step 3 does a handler call `list_documents`, `add_document`,
   `RAGChain.answer` or `CaseReasoningChain.analyze`, always with the matter id that the
   dependency returned, never one taken from the request body.

Writes to the read-only shared matter return `403 matter_read_only`; the ingestion module
enforces the same rule a second time (`SharedMatterReadOnlyError`).

## Document processing

Upload -> validate suffix (`.pdf`, `.docx`) and size -> write to a temp dir -> create a
job (`queued`) -> return `202` with the job -> FastAPI `BackgroundTasks` runs
`add_document` under a process-wide lock (`processing`) -> `ready` or `failed` with a
curated failure message -> retriever caches for that matter are invalidated. The job
table is in memory: a restart forgets in-flight jobs, but documents that reached `ready`
are persisted by the ingestion module and reappear in the list. This is the MVP stand-in
for the job queue in the long-term design.

Web uploads index the original text and do not translate it. Translating every
chunk through Claude held the document in `processing` for the whole file, so it
could not be reviewed, and it spent the user's key before any question. Language
detection still runs. `TRANSLATE_ON_INGEST` still applies to the Streamlit app and
the ingestion scripts (default unchanged). The upload handler still resolves the
uploader's key and passes it to `add_document(api_key=...)` for anything else that
call spends a key on.

## Chat jobs and progress stages (revision 3)

`POST .../chat` stays synchronous (tests and simple clients keep using it). The browser
client calls `POST .../chat/jobs` instead, which runs the same `run_chat` on a small
thread pool (`api/chat_jobs.py`) and returns a job id; `GET .../chat/jobs/{id}` is
answered only when the job belongs to the same matter *and* the same user, otherwise
`404 not_found`.

Progress comes from `policy_advisor/progress.py`: a `contextvars.ContextVar` listener.
The job thread installs a listener around `run_chat`; the pipeline calls
`report_stage("reading_documents" | "searching_web" | "writing" | "checking_citations")`
at the point where it starts retrieving, falls back to the web, calls the model or
runs the faithfulness/judge check. Without a listener the calls are no-ops, so the
Streamlit prototype, the eval harness and the synchronous endpoints are unaffected.
The stages are the client's only source for the bubble narration, so the bubble
describes work that is actually happening.

## Answers, citations and the avatar

`POST .../ask` calls `RAGChain.answer` and maps the result to the contract: the `[Source:
locator]` tags Claude wrote are resolved against the chunks that were actually retrieved
(same exact-or-prefix rule as `faithfulness.py`); only resolved locators become
citations, so a citation can never point at text the model did not see (rule 3). Each
citation carries `kind`: `authority` for statute and judgment chunks, `evidence` for
everything else a user uploads, `web` for `web_search` results (rules 4 and 5). Web
answers are returned with `source: "web"` and never carry `evidence` citations.

`POST .../analyze` calls `CaseReasoningChain.analyze` and resolves each issue's cited
locators the same way, using a retriever query identical to the one the chain ran.

`avatar_state` is computed in one function (`api/avatar.py`) from `source` and
`faithful` and is the only thing the client uses to pick a frame; the client never
derives trust from answer text.

## Chat flow (revision 2)

The chat-first client sends every message to `POST .../chat` and the server decides
what to run (`routers/chat.py`, `decide_mode`). The rules are deliberately small so a
non-technical user never has to pick a mode:

| Situation                                                                      | Mode       | Pipeline                                   |
| ------------------------------------------------------------------------------ | ---------- | ------------------------------------------ |
| First message of an unanalysed conversation, or the "Analyse my case" chip, and the matter has documents | `analysis` | `run_analyze` (CaseReasoningChain)         |
| Same, but the matter has no documents                                          | `research` | `run_ask` with `allow_web` forced on, labelled as research |
| Anything else (including every message to the shared read-only library)        | `question` | `run_ask` with the caller's `allow_web`    |

Research mode is how rules 4 and 5 survive a user who describes a case before uploading
anything: there is no evidence to retrieve, so the reply says so in the bubble and in a
`notice`, its citations can only be `web`, and the only follow-up offered is to add
documents. It never pretends to be an analysis of the case.

Each reply carries a one-sentence `bubble`, `avatar_state`, citations and follow-up
`chips`; the full ask/analyze payload rides along unchanged so the transcript renders
citations with the same chips and passage dialog as before.

Conversations are persisted per owned matter in `conversation.json`
(`api/conversations.py`) so "Your cases" can reopen them and so the server can tell a
first message from a follow-up. The shared library gets no log: several users talk to
it and their transcripts must not mix, so the client keeps that one in memory. The
first user message also derives the matter `title` (first ~60 characters, word
boundary), stored in `meta.json` next to `owner`.

The client creates matters behind the scenes (`POST /api/matters` with no id; the server
generates `case-<hex>`), remembers the current one in `sessionStorage`, and never shows
an id. Everything the client does still goes through `get_matter`.

## What is new versus reused

| New (this MVP)                                   | Reused unchanged                        |
| ------------------------------------------------ | --------------------------------------- |
| `src/policy_advisor/api/` (app, auth, deps, jobs, chat, conversations) | ingestion, retrieval, generation, eval |
| `web/` static client                              | `scripts/add_user.py`, credentials file |
| `docs/architecture/`, `docs/contracts/`, doc 17   | `assets/avatar/` masters (mounted)      |

Changes to existing modules are limited to `config.py` (three new settings, an empty
default for the API key so the server can start and report "model not configured", and
`env_ignore_empty=True` so an empty exported variable cannot shadow `.env`),
`chunk_translation.py` (the `TRANSLATE_ON_INGEST` switch), and `matter_store.py`
(`get_matter_meta`/`update_matter_meta`: optional `title`, `created_at`, `updated_at` in
the existing `meta.json`, `owner` untouched). All are flagged in the PR.

Revision 3 adds, all backwards compatible (every new parameter is optional and falls
back to `settings`):

| Module                              | Change                                                                      |
| ----------------------------------- | --------------------------------------------------------------------------- |
| `generation/chain.py`               | `RAGChain(api_key=None, retriever=None)`; `report_stage` at retrieve / web fallback / model call / faithfulness |
| `generation/case_reasoning.py`      | `CaseReasoningChain(api_key=None, retriever=None)`; `report_stage` per issue and at the judge check |
| `generation/web_search.py`          | `search_official_sources(..., api_key=None)`                                |
| `ingestion/translate.py`            | `get_translation_llm(api_key=None)`                                         |
| `ingestion/chunk_translation.py`    | `translate_chunks(chunks, api_key=None, translate=None)`                    |
| `ingestion/ingest_document.py`      | `add_document(..., *, api_key=None, translate=None)`                        |
| `ingestion/matter_store.py`         | `meta.json` and `chunks.json` are written atomically (temp file + `os.replace`) because a chat job thread can now read a matter while a request thread writes it |
| `config.py`                         | `allow_shared_anthropic_key` (`ALLOW_SHARED_ANTHROPIC_KEY`), `auth_cookie_key_is_insecure_default()` |
| `progress.py` (new)                 | `report_stage`, `stage_listener`                                            |
| `pyproject.toml`                    | `cryptography` (Fernet) added                                               |

## Known limitations of this slice

Single-process job tables (document and chat); the key-check rate limiter is in memory
per process; user keys are bound to `AUTH_COOKIE_KEY` (rotating it means re-entering
keys); no password reset or sign-up over the API; no rate limiting
on login; the web fallback returns URLs without verbatim quoted spans; evidence versus
authority is inferred from the prototype's `doc_type` rather than from a curated
library; the conversation log is a JSON file per matter (the storage PR should move it
with `chunks.json` and `meta.json`). See `docs/17-web-app-mvp.md` for the full list and
how to run it.
