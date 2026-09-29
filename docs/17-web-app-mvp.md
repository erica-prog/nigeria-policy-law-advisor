# 17 — Web app MVP: running it, adding users, limitations

The first end-to-end web slice: a FastAPI backend (`src/policy_advisor/api/`) wrapping
the existing retrieval, faithfulness and case-reasoning pipeline, plus a dependency-free
browser client (`web/`) with the advisor character from doc 15. Design:
`docs/architecture/web-mvp.md`. Contract: `docs/contracts/web-api.md`.

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
  is visible to every user, read-only, exactly as in the prototype.
- Without `ANTHROPIC_API_KEY` the server still starts: `/api/health` reports
  `llm_configured: false`, the UI shows a banner, and ask/analyze return
  `503 llm_unavailable`. Login, matters and uploads work. Set
  `TRANSLATE_ON_INGEST=false` to upload documents offline (ingestion otherwise calls
  Claude to translate every chunk).
- Docker: `docker compose up api` (port 8000). `docker compose up app` still runs the
  Streamlit prototype on 8501.

## What you can do

1. Log in / log out (signed HttpOnly cookie, 7 days).
2. Create matters (ids are slugs such as `foxglove-v-acme-2026`) and open them. You only
   ever see your own matters plus the shared demo.
3. Upload PDF or DOCX into a matter. Processing runs in the background; the document list
   shows `queued`, `processing`, `ready` or `failed` with a short reason. Remove documents.
4. Ask questions. Answers show `[Source: …]` tags as clickable chips; a chip opens the
   passage (document, locator, page, verbatim quote) and is labelled **case evidence**,
   **legal authority** or **web source**. Tick "Search official websites…" to allow the
   web fallback; such answers are visibly amber and labelled as unverified web material.
5. Analyze a case: issues, arguments per side with resolved authorities, assessment,
   confidence, overall position, "missing support" for issues with nothing retrieved,
   and the mandatory disclaimer.
6. The advisor character changes state with every response and announces the state in
   a caption (`aria-live`). Hide it with "Hide character"; the interface stays complete.

## Avatar implementation notes (doc 15)

- Frames are served straight from `assets/avatar/` (mounted at `/avatar`); nothing is
  duplicated or generated.
- All four frames are cropped with one shared box, `(165, 17, 1170, 1219)` on the
  1254×1254 canvas (1005×1202), applied as CSS offsets from `web/js/avatar.js`, so the
  character keeps the same size and position when the frame changes. Recompute the box
  with the snippet in doc 15 if a frame is added or redrawn.
- `web_source` and `unverified` have no artwork yet: idle frame plus amber/red outline
  and caption, as doc 15 prescribes. Smooth downscaling only; no `image-rendering:
  pixelated`; 150 ms crossfade disabled under `prefers-reduced-motion`.
- Captions mirror the banners baked into the PNGs ("Verified source: citations checked
  against your documents"). Doc 15's wording concern about "VERIFIED" stands and is a
  decision for the lawyer; changing the text is a one-line edit in `avatar.js`.

## Where things are

| Piece                         | Location                                          |
| ----------------------------- | ------------------------------------------------- |
| App factory / entrypoint      | `src/policy_advisor/api/main.py` (`app`)          |
| Session cookie, credentials   | `api/sessions.py`, `api/credentials.py`           |
| Permission dependency         | `api/deps.py` (`get_matter`, `get_writable_matter`) |
| Routers                       | `api/routers/{auth,matters,documents,advice}.py`  |
| Job table                     | `api/jobs.py`                                     |
| Citation mapping              | `api/citations.py`                                |
| Avatar state mapping          | `api/avatar.py`                                   |
| Browser client                | `web/index.html`, `web/styles.css`, `web/js/*.js` |
| Tests                         | `tests/api/`                                      |

## Limitations

- **Auth**: username/password against the local YAML file; no sign-up, password reset,
  rate limiting or session revocation list. The long-term design uses a managed provider.
- **Jobs**: in-process table. A restart forgets queued/processing/failed entries; `ready`
  documents persist. One ingestion runs at a time.
- **Storage**: Chroma + JSON files under `data/index`, as on `main`. The API only calls
  `ingest_document`, `matter_store`, `HybridRetriever`, `RAGChain` and
  `CaseReasoningChain`, so the storage PR can land underneath.
- **Evidence vs authority** is inferred from the prototype's `doc_type` (`statute`,
  `judgment` → authority; everything else → evidence). A curated library scope does not
  exist yet.
- **Web fallback** returns titles and URLs, not verbatim quoted spans, and is labelled
  accordingly.
- **Analysis** exposes what `CaseReasoningChain` produces. `contradictions` and
  `counterarguments` are reserved fields and always empty; `missing_evidence` lists issues
  with no retrieved support.
- **Answer text** is rendered as plain text with citation chips; no markdown rendering.
- Uploaded files are held in a temporary directory only during processing; the original
  file is not kept, so citations open the extracted passage, not the original page image.
