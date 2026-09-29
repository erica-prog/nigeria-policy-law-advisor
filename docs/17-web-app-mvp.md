# 17 — Web app MVP: running it, adding users, limitations

The first end-to-end web slice: a FastAPI backend (`src/policy_advisor/api/`) wrapping
the existing retrieval, faithfulness and case-reasoning pipeline, plus a dependency-free
browser client (`web/`) built around the advisor character from doc 15. Design:
`docs/architecture/web-mvp.md`. Contract: `docs/contracts/web-api.md`.

Revision 2 turned the client into a single chat-style screen for non-technical users:
the character sits in the middle with a speech bubble, documents are added with a `+`
button, and the server decides whether a message is a case to analyse or a question.

## Run locally

```bash
uv sync
cp .env.example .env          # set ANTHROPIC_API_KEY and a random AUTH_COOKIE_KEY
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
- Without `ANTHROPIC_API_KEY` the server still starts: `/api/health` reports
  `llm_configured: false`, the character says so in plain words, and ask/analyze/chat
  return `503 llm_unavailable`. Login, cases and uploads work. Set
  `TRANSLATE_ON_INGEST=false` to upload documents offline (ingestion otherwise calls
  Claude to translate every chunk).
- Docker: `docker compose up api` (port 8000). `docker compose up app` still runs the
  Streamlit prototype on 8501.

## Accounts

There is no sign-up or password reset in this version. Whoever runs the server creates
accounts:

```bash
uv run python -m scripts.add_user <username> "<Display Name>"
```

The script prompts for the password twice (it never appears in shell history) and writes
a bcrypt hash to `data/auth/credentials.yaml`. The login card tells users to ask their
administrator for an account and says clearly when a username or password is wrong.

## Claude key not detected?

If the character says *"I can't think yet: the server has no Claude API key"* although
you set one:

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
   `{"status": "ok", "llm_configured": true}` when a key is loaded. The key itself is
   never logged or returned.

## What you can do

1. Log in / log out (signed HttpOnly cookie, 7 days).
2. Talk to the advisor. The first thing you type starts a new case behind the scenes; the
   case is titled from your words and listed in the **Your cases** drawer, where you can
   switch cases or start a new one. You never see ids or slugs.
3. Add documents with the `+` button (PDF or DOCX, several at once). Each file shows as a
   chip with its status (`queued`, `processing`, `ready`, `failed` with a short reason)
   and can be removed. When processing finishes the character offers **Analyse my case
   now**.
4. Describe your case. With documents in the case the advisor runs the full case
   analysis: issues, arguments per side with resolved authorities, assessment,
   confidence, overall position, missing support and the disclaimer. With no documents
   it runs **research mode** instead: general legal research from official web sources
   and the model, labelled plainly as *not evidence from your documents*, with a chip to
   add them.
5. Ask questions. Answers cite only passages retrieved from your documents; `[Source: …]`
   tags become clickable chips labelled **case evidence**, **legal authority** or **web
   source** that open the verbatim passage. Switch on **Also search official web
   sources** to allow the web fallback; such answers are amber and labelled as
   unverified web material.
6. The character changes state with every reply and announces it in a caption
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
| Routers                       | `api/routers/{auth,matters,documents,advice,chat}.py` |
| Chat flow decision            | `api/routers/chat.py` (`decide_mode`)             |
| Conversation log, titles      | `api/conversations.py`                            |
| Job table                     | `api/jobs.py`                                     |
| Citation mapping              | `api/citations.py`                                |
| Avatar state mapping          | `api/avatar.py`                                   |
| Browser client                | `web/index.html`, `web/styles.css`, `web/js/*.js` |
| Tests                         | `tests/api/`, `tests/test_config_env.py`          |

## Limitations

- **Auth**: username/password against the local YAML file; no sign-up, password reset,
  rate limiting or session revocation list. The long-term design uses a managed provider.
- **Jobs**: in-process table. A restart forgets queued/processing/failed entries; `ready`
  documents persist. One ingestion runs at a time.
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
