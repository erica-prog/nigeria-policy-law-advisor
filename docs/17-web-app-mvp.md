# 17 — Web app MVP: running it, adding users, limitations

The first end-to-end web slice: a FastAPI backend (`src/policy_advisor/api/`) wrapping
the existing retrieval, faithfulness and case-reasoning pipeline, plus a dependency-free
browser client (`web/`) built around the advisor character from doc 15. Design:
`docs/architecture/web-mvp.md`. Contract: `docs/contracts/web-api.md`.

Revision 2 turned the client into a single chat-style screen for non-technical users:
the character sits in the middle with a speech bubble, documents are added with a `+`
button, and the server decides whether a message is a case to analyse or a question.

Revision 3 makes every user bring their own Claude key (so the person running the
server never pays for other people's questions) and turns the speech bubble into a live
commentary: the character reacts to documents as they arrive and narrates each stage of
an answer while it is being produced.

## Run locally

```bash
uv sync
cp .env.example .env          # set a random AUTH_COOKIE_KEY (required for user keys)
uv run python -m scripts.add_user jdoe "Jane Doe"     # prompts for a password
uv run uvicorn policy_advisor.api.main:app --reload
```

Open <http://127.0.0.1:8000/>. The OpenAPI document is at `/docs`.

- Existing accounts from the Streamlit prototype work unchanged: both read
  `data/auth/credentials.yaml` (bcrypt hashes written by `scripts/add_user.py` or the
  Streamlit sign-up tab).
- The shared `phase1-demo` matter (built by `python -m policy_advisor.ingestion.build_index`)
  appears in "Your cases" as **Reference library (shared, read-only)** for every user,
  exactly as in the prototype.
- `ANTHROPIC_API_KEY` in `.env` is now optional for the web app: users bring their own
  key (next section). The server key is still used by the Streamlit prototype, the
  ingestion scripts and the eval harness, and by web users only when
  `ALLOW_SHARED_ANTHROPIC_KEY=true`. Without any usable key the server still starts:
  login, cases and uploads work, ask/analyze/chat return `403 claude_key_required`, and
  ingestion skips translation for that user's uploads (language detection still runs).
  `TRANSLATE_ON_INGEST=false` still switches translation off for everyone.
- Docker: `docker compose up api` (port 8000). `docker compose up app` still runs the
  Streamlit prototype on 8501.

## Bring your own Claude key (revision 3)

Every user of the web app pays Anthropic directly for what Claude reads and writes for
them. The first time someone logs in the character says *"I need your Claude key before
I can start thinking."*, the message box is disabled with the placeholder *Activate the
advisor to start*, and a chip **Activate the advisor** opens a dialog with four steps:

1. Create an account at console.anthropic.com.
2. Add a payment method under *Billing*: the user pays Anthropic, typically a few cents
   per question; the person running this app never pays for their usage and never sees
   it.
3. Create an API key under *API Keys* and copy it (it starts with `sk-ant-`).
4. Paste it into the dialog and press **Save and activate**.

The `+` button, history and the **Your cases** drawer keep working without a key, so
people can add documents first. On save the server checks the key's format, verifies it
with the cheapest real call to Anthropic (`models.list(limit=1)`), stores it and
replies with the last four characters only; the character says *"Thank you, I'm ready.
Tell me about your case, or add your documents with +"*. Wrong keys and unreachable
Anthropic are reported inside the dialog in plain words; five attempts per minute are
allowed per user.

What is stored and where:

- `data/auth/user_keys.json`, next to `credentials.yaml` (it follows the same directory
  relocations and test temp dirs), one entry per username, file mode `0600`, written
  under a file lock.
- Each key is encrypted with Fernet (AES-128-CBC + HMAC) under a key derived from
  `AUTH_COOKIE_KEY` with HKDF-SHA256. Rotating `AUTH_COOKIE_KEY` therefore invalidates
  stored keys: users are asked to paste theirs again, nothing is ever decrypted into a
  log. The server refuses to store keys while `AUTH_COOKIE_KEY` is still the insecure
  default (`503 server_not_configured`), so set it before inviting users.
- The key is never returned by any endpoint, never logged and never included in an
  error message. The **Claude key** section of the drawer shows *Key on file, ends in
  …abcd* with **Replace** and **Remove** (confirmed); or *No key* with **Activate**.

`ALLOW_SHARED_ANTHROPIC_KEY` (default `false`): when `true`, users without a key of
their own use the server's `ANTHROPIC_API_KEY`. The greeting then says so and offers
**Use my own key**; `/api/me` reports `key_source: "shared"`. Leave it off for any
deployment where you do not want to pay for other people's questions. The server's key
never reaches the browser in either mode; only the chosen key's *source* is reported.

Web uploads are indexed from the original text and are not translated, so a document
can be reviewed as soon as it is read. `TRANSLATE_ON_INGEST` still applies to the
Streamlit app and the ingestion scripts.

## The live speech bubble (revision 3)

The bubble is a running commentary, one short sentence at a time; the full answer is
always rendered as normal text in the conversation:

- Adding files: *"Got it, I'll read supply-agreement.docx"* (or *"3 documents"*), then
  *"I've read supply-agreement.docx"* per file, a friendly sentence with the curated
  reason when one fails, and **Analyse my case now** once everything is ready.
- Sending: *"Let me think about that…"* with a typing indicator and the `listening`
  frame. The client runs the message as a chat job and polls it every 700 ms; the bubble
  follows the stage the server reports: *Reading your documents…*, *Checking official
  websites…*, *Checking my citations against the sources…*, *Writing it up…*. The
  stages are emitted where the work happens (retrieval, web fallback, model call,
  faithfulness/judge check), so they are true, not a scripted animation.
- The web toggle: *"I'll also check official websites, and label anything from them"*
  / *"I'll stick to your documents"*. A new case: *"A fresh case. Tell me what happened,
  or add documents with +"*.
- Accessibility: the visible bubble is decorative for screen readers; a visually hidden
  `aria-live="polite"` region receives the same text throttled to at most one
  announcement every two seconds, so stage changes do not talk over each other. The
  caption under the character keeps its own `aria-live`. Every animation (bubble
  pop, typing dots, avatar bounce, message entrance) is disabled under
  `prefers-reduced-motion`.

## Accounts

There is no sign-up or password reset in this version. Whoever runs the server creates
accounts:

```bash
uv run python -m scripts.add_user <username> "<Display Name>"
```

The script prompts for the password twice (it never appears in shell history) and writes
a bcrypt hash to `data/auth/credentials.yaml`. The login card tells users to ask their
administrator for an account and says clearly when a username or password is wrong.

## Server Claude key not detected?

This section is about the **server's** key in `.env` (used by the Streamlit prototype,
ingestion scripts, and by web users only under `ALLOW_SHARED_ANTHROPIC_KEY=true`). Web
users who see *"I need your Claude key before I can start thinking."* simply have not
added their own yet: see *Bring your own Claude key* above. If `/api/health` reports
`llm_configured: false` although you set a server key:

1. **Where the file must live.** The settings loader reads exactly one file:
   `.env` in the repository root, next to `pyproject.toml`. A `.env` inside `src/`,
   `web/` or your home directory is ignored. Start from `.env.example`.
2. **Restart the server.** Settings are read once at start-up (`get_settings()` is
   cached). `uvicorn --reload` restarts on code changes, not on `.env` changes.
3. **An empty exported variable.** `export ANTHROPIC_API_KEY=` in your shell profile used
   to override the file with an empty string. Since revision 2 the loader ignores empty
   environment values (`env_ignore_empty=True` in `config.py`), so the file wins; a
   non-empty shell variable still takes precedence over the file, so check
   `echo "$ANTHROPIC_API_KEY"` if the server uses a key you did not expect.
4. **Docker.** `docker-compose.yml` passes the file through `env_file: .env`, so the same
   root `.env` applies; rebuild is not needed, but restart the container after editing.
5. **Check what the server sees.** `curl http://127.0.0.1:8000/api/health` returns
   `{"status": "ok", "llm_configured": true, "shared_key_allowed": false}` when a key
   is loaded. The key itself is never logged or returned.

## What you can do

1. Log in / log out (signed HttpOnly cookie, 7 days).
2. Activate the advisor with your own Claude key (once; replace or remove it from the
   drawer).
3. Talk to the advisor. The first thing you type starts a new case behind the scenes; the
   case is titled from your words and listed in the **Your cases** drawer, where you can
   switch cases or start a new one. You never see ids or slugs.
4. Add documents with the `+` button (PDF or DOCX, several at once). Each file shows as a
   chip with its status (`queued`, `processing`, `ready`, `failed` with a short reason)
   and can be removed. When processing finishes the character offers **Analyse my case
   now**.
5. Describe your case. With documents in the case the advisor runs the full case
   analysis: issues, arguments per side with resolved authorities, assessment,
   confidence, overall position, missing support and the disclaimer. With no documents
   it runs **research mode** instead: general legal research from official web sources
   and the model, labelled plainly as *not evidence from your documents*, with a chip to
   add them.
6. Ask questions. Answers cite only passages retrieved from your documents; `[Source: …]`
   tags become clickable chips labelled **case evidence**, **legal authority** or **web
   source** that open the verbatim passage. Switch on **Also search official web
   sources** to allow the web fallback; such answers are amber and labelled as
   unverified web material.
7. The character changes state with every reply and announces it in a caption
   (`aria-live`); the speech bubble gives a one-sentence summary and follow-up chips.
   Hide the character with "Hide character"; the interface stays complete.

## Avatar implementation notes (doc 15)

- Frames are served straight from `assets/avatar/` (mounted at `/avatar`); nothing is
  duplicated or generated.
- All four frames are cropped with one shared box, `(165, 17, 1170, 1219)` on the
  1254×1254 canvas (1005×1202), applied as CSS offsets from `web/js/avatar.js`, so the
  character keeps the same size and position when the frame changes. Recompute the box
  with the snippet in doc 15 if a frame is added or redrawn.
- `web_source` and `unverified` have no artwork yet: idle frame plus amber/red outline
  and caption, as doc 15 prescribes. Smooth downscaling only; no `image-rendering:
  pixelated`; 150 ms crossfade, bounce on state change and bubble pop-in are all
  disabled under `prefers-reduced-motion`.
- Captions mirror the banners baked into the PNGs ("Verified source: citations checked
  against your documents"). Doc 15's wording concern about "VERIFIED" stands and is a
  decision for the lawyer; changing the text is a one-line edit in `avatar.js`.
- Doc 15 argued against a speech bubble; revision 2 adds one at the user's request. It
  holds one short sentence and chips only. The full answer is always rendered as normal
  text in the conversation, never inside the bubble, so it can be read and copied.

## Where things are

| Piece                         | Location                                          |
| ----------------------------- | ------------------------------------------------- |
| App factory / entrypoint      | `src/policy_advisor/api/main.py` (`app`)          |
| Session cookie, credentials   | `api/sessions.py`, `api/credentials.py`           |
| Permission dependency         | `api/deps.py` (`get_matter`, `get_writable_matter`) |
| Routers                       | `api/routers/{auth,matters,documents,advice,chat,keys}.py` |
| Chat flow decision            | `api/routers/chat.py` (`decide_mode`)             |
| Conversation log, titles      | `api/conversations.py`                            |
| Document job table            | `api/jobs.py`                                     |
| Chat job table (stages)       | `api/chat_jobs.py`; stages emitted via `policy_advisor/progress.py` |
| User Claude keys              | `api/user_keys.py` (encryption, validation, rate limit) |
| Per-key chain cache           | `api/services.py` (`rag_chain_for`, `case_chain_for`) |
| Activate dialog (client)      | `web/js/activate.js`; bubble in `web/js/bubble.js` |
| Citation mapping              | `api/citations.py`                                |
| Avatar state mapping          | `api/avatar.py`                                   |
| Browser client                | `web/index.html`, `web/styles.css`, `web/js/*.js` |
| Tests                         | `tests/api/`, `tests/test_config_env.py`          |

## Limitations

- **Auth**: username/password against the local YAML file; no sign-up, password reset,
  rate limiting or session revocation list. The long-term design uses a managed provider.
- **Jobs**: in-process tables. A restart forgets queued/processing/failed document
  entries (`ready` documents persist) and every in-flight chat job (the client then
  shows the generic "could not finish" message; the conversation log only records
  finished replies). One ingestion runs at a time; chat jobs run on a small thread pool.
- **User keys**: encrypted with a key derived from `AUTH_COOKIE_KEY`, so rotating that
  secret logs everyone out *and* makes them paste their key again. The key-check rate
  limit is in memory per process. A user's key is used for their uploads' translation
  and their questions only; there is no per-user spend view (Anthropic's console has
  it).
- **Storage**: Chroma + JSON files under `data/index`, as on `main`, plus
  `conversation.json` per matter. The API only calls `ingest_document`, `matter_store`,
  `HybridRetriever`, `RAGChain` and `CaseReasoningChain`, so the storage PR can land
  underneath; it should move the conversation log with the other two sidecars.
- **Evidence vs authority** is inferred from the prototype's `doc_type` (`statute`,
  `judgment` → authority; everything else → evidence). A curated library scope does not
  exist yet.
- **Web fallback** returns titles and URLs, not verbatim quoted spans, and is labelled
  accordingly. Research mode (no documents) is the same fallback with clearer labelling.
- **Analysis** exposes what `CaseReasoningChain` produces. `contradictions` and
  `counterarguments` are reserved fields and always empty; `missing_evidence` lists issues
  with no retrieved support.
- **Flow heuristics** are simple on purpose: the first message of a new case is treated
  as a case description. A user who opens with a question gets research mode or an
  analysis rather than a plain answer; the follow-up is a normal question.
- **Shared library** conversations are not stored (several users share it), so switching
  away and back loses that transcript.
- **Answer text** is rendered as plain text with citation chips; no markdown rendering.
- Uploaded files are held in a temporary directory only during processing; the original
  file is not kept, so citations open the extracted passage, not the original page image.
