# 12 — Voice Web App Architecture

This is the first of three documents on the spoken, cartoon-avatar interface:

- **12 (this file)** — the client/server boundary: what runs where, the HTTP
  surface, auth, and the latency budget.
- [13-voice-pipeline.md](13-voice-pipeline.md) — speech-to-text and
  text-to-speech, and the legal-specific failure modes voice introduces.
- [14-cartoon-avatar-frontend.md](14-cartoon-avatar-frontend.md) — the
  avatar, lip-sync, and the TypeScript in the browser.

Per [CLAUDE.md](../CLAUDE.md) this is guidance for you to apply. Nothing
described here has been built; the snippets are illustrative shapes, not
implementations to paste.

## What's actually being added

Almost nothing in the retrieval or generation pipeline changes. `RAGChain.answer()`
in [chain.py](../src/policy_advisor/generation/chain.py) already returns
everything a voice client needs, including the two fields that carry trust
information (`source` and `faithful`). What's missing is a transport and a
client:

```
┌─ Browser (TypeScript) ────────────┐      ┌─ FastAPI (Python) ─────────┐
│                                    │      │                             │
│  push-to-talk mic → audio blob ────┼─────▶│  POST /api/transcribe        │
│                                    │      │      ↓ (Whisper, doc 13)    │
│  transcript shown for correction ◀─┼──────┤   { text }                   │
│            │                       │      │                             │
│            └── confirmed text ─────┼─────▶│  POST /api/ask               │
│                                    │      │      ↓                      │
│                                    │      │   RAGChain.answer()          │
│                                    │      │   (existing, unchanged)      │
│  answer + citations + source  ◀────┼──────┤   AnswerResult as JSON       │
│            │                       │      │                             │
│            └── answer text ────────┼─────▶│  POST /api/speak             │
│  audio ◀───────────────────────────┼──────┤   audio/mpeg + visemes       │
│    ↓                               │      │                             │
│  AnalyserNode → avatar mouth       │      └─────────────────────────────┘
│  captions + citation panel         │
└────────────────────────────────────┘
```

The ingestion pipeline, vector store, BM25 index, and matter isolation all
sit behind `/api/ask` exactly as they do behind the Streamlit app today.

## Why Streamlit can't host this

[07-demo-ui.md](07-demo-ui.md) recommended Streamlit first, and that was the
right call — it got retrieval quality in front of the lawyer fast. Voice is
where it runs out. The blockers are structural, not cosmetic:

- **No microphone access.** Streamlit's execution model is server-side script
  reruns; capturing a `MediaStream` requires JavaScript running in the page
  with a persistent object across frames. Community components exist, but
  you'd be fighting the framework for the core interaction of the product.
- **No control over audio playback.** You need to know *when* playback
  starts, per-frame amplitude while it plays, and when it ends, because the
  avatar's mouth is driven off that signal (doc 14). `st.audio` gives you a
  player widget, not events.
- **The rerun model fights animation.** Every interaction re-executes
  `app.py` top to bottom. An avatar that animates continuously through
  "listening → thinking → speaking" needs state that survives independently
  of that, in the browser.

Keep the Streamlit app. It is the better tool for the work it already does —
ingesting documents, managing matters, and running the multi-minute case
analysis — and it stays useful as the internal/debug surface after the voice
client exists. Don't plan to delete it.

## Why FastAPI, and not the TypeScript backend doc 07 sketched

[07-demo-ui.md](07-demo-ui.md) Option B sketched an Express/Fastify backend
exposing `POST /ask`. That advice predates the code that now exists, and it
should be superseded here. The reason is concrete: the entire orchestration
layer — `RAGChain`, `HybridRetriever`, `check_faithfulness`, the Chroma
client, the sentence-transformers embedding model — is Python. A TypeScript
backend cannot call any of it in-process. It would have to shell out to
Python or call a second Python HTTP service, which means two deployables and
one extra network hop for zero benefit.

FastAPI imports `RAGChain` directly:

```python
# illustrative shape only
from policy_advisor.generation.chain import RAGChain

chain = RAGChain()  # load once at startup, like @st.cache_resource does today

@app.post("/api/ask")
async def ask(body: AskRequest, user: User = Depends(current_user)) -> AskResponse:
    result = chain.answer(
        body.question,
        matter_id=body.matter_id,
        jurisdiction=body.jurisdiction,
        conversation_language=body.conversation_language,
        allow_web_fallback=body.allow_web_fallback,
    )
    ...
```

Two things worth knowing before you build it:

- `RAGChain.__init__` loads the embedding model and opens Chroma. Construct
  it **once** at application startup (FastAPI's `lifespan` handler), not per
  request. The Streamlit app gets this right via `@st.cache_resource`
  ([app.py](../src/policy_advisor/app.py) lines 39–41); the FastAPI version
  needs its own equivalent or every question pays model-load cost.
- `chain.answer()` is **synchronous and slow** (seconds). Declaring the
  endpoint `async def` and calling a blocking function inside it will stall
  the whole event loop. Either declare the endpoint `def` (FastAPI runs sync
  endpoints in a threadpool automatically) or wrap the call in
  `anyio.to_thread.run_sync`. This is the single most common way a
  Python async web app ends up serving one user at a time.

TypeScript still has a real job here: the entire browser client is TS
(doc 14). You get the language exposure without paying for a cross-language
boundary in the hot path.

## The endpoint surface

| Endpoint | Purpose | Notes |
|---|---|---|
| `POST /api/auth/login` | Exchange credentials for a session cookie | See auth section below |
| `POST /api/auth/logout` | Clear the session | |
| `GET /api/matters` | Matters visible to this user | Wraps `list_matters_for_user` |
| `GET /api/matters/{id}/documents` | Documents in a matter | Wraps `list_documents` |
| `POST /api/matters/{id}/documents` | Upload + ingest | Wraps `add_document`; must call `chain.invalidate_matter` |
| `DELETE /api/matters/{id}/documents/{name}` | Remove | Wraps `remove_document`; same invalidation |
| `POST /api/transcribe` | Audio blob → text | Doc 13 |
| `POST /api/ask` | Question → grounded answer | Wraps `RAGChain.answer` |
| `POST /api/speak` | Answer text → audio (+ viseme timings) | Doc 13 |

The document endpoints matter more than they look. `add_document` and
`remove_document` must be followed by `chain.invalidate_matter(matter_id)`,
or the cached per-matter BM25 index keeps serving the pre-upload chunk list —
the Streamlit app does this at
[app.py](../src/policy_advisor/app.py) lines 82 and 101, and it is easy to
forget when reimplementing the same flow behind HTTP. With a single-process
FastAPI app the in-memory invalidation works the same way it does in
Streamlit. The moment you run more than one worker it does not: worker A
invalidates its own cache and worker B keeps the stale one. If you scale out,
that cache has to move somewhere shared or become version-stamped.

### The response contract is the part to get right

Mirror `AnswerResult`
([chain.py](../src/policy_advisor/generation/chain.py) lines 29–42) field for
field:

```json
{
  "answer": "Under Order 5 Rule 2(1) ...",
  "source": "corpus",
  "faithful": true,
  "unsupported_citations": [],
  "retrieved": [
    {
      "locator": "Order 5 Rule 2",
      "doc_type": "rules",
      "jurisdiction": "federal",
      "source_document": "FHC Civil Procedure Rules.pdf",
      "page": 14,
      "text": "..."
    }
  ],
  "web_citations": [],
  "usage": { "input_tokens": 3412, "output_tokens": 288 }
}
```

The temptation, building a voice product, is to collapse this to
`{ "answer": "..." }` because that's all the speech synthesiser needs. Resist
it. `source` distinguishes a faithfulness-checked answer from your own
documents, an unverified answer from an official website, and "found
nothing" — three things the Streamlit app renders very differently
([app.py](../src/policy_advisor/app.py) lines 150–168) and which the avatar
and the spoken preamble must also differentiate (docs 13 and 14). A voice
client that throws `source` and `faithful` away has silently removed the
product's honesty mechanism. Treat those two fields as required, not
decorative.

`retrieved` carries the citation panel's content and can be large. Send the
metadata always; consider sending chunk `text` only on demand via a separate
`GET /api/chunks/{chunk_id}` if payload size becomes a problem — but don't
drop the locators.

## Auth is the one real refactor

`require_login()` in [auth.py](../src/policy_advisor/auth.py) (lines 84–110)
cannot be reused as-is. It's Streamlit all the way down: it renders
`st.tabs`, calls `authenticator.login(location="main")`, reads
`st.session_state["username"]`, and calls `st.stop()` to halt page rendering.
None of that means anything to an HTTP handler.

What *is* reusable is the part underneath:

- `load_credentials()` / `save_credentials()` — the bcrypt-hashed
  `credentials.yaml` store, including the `FileLock` that stops concurrent
  registrations clobbering each other (lines 37–45).
- The `.com`-only email rule (`_COM_EMAIL_RE`, lines 28 and 71).

So the refactor is: extract a framework-free
`verify_password(username, password) -> bool` that reads the same YAML and
checks the bcrypt hash, then build both surfaces on top of it — Streamlit
keeps `require_login()`, FastAPI gets a login endpoint that sets a signed
session cookie. Two presentation layers, one credential store. If you instead
write a second credential path for the API, you will eventually have two
places where a password can be checked and only one of them will get the next
security fix.

For the session itself, a signed HTTP-only cookie is sufficient and simpler
than JWTs for a single-backend app. Reuse `AUTH_COOKIE_KEY` from
[config.py](../src/policy_advisor/config.py) so there's one signing secret,
and keep the cookie `HttpOnly`, `Secure`, and `SameSite=Lax` — the browser
client never needs to read it in JavaScript.

### Matter isolation must be re-checked server-side

This is the security consequence of moving to an API, and it's worth stating
plainly. Today, the only thing stopping a lawyer querying someone else's
matter is that the sidebar's dropdown is populated from
`list_matters_for_user(username)` ([app.py](../src/policy_advisor/app.py)
lines 52–53). The `matter_id` never leaves the server, so a UI-level gate is
an adequate gate.

Once `matter_id` arrives in a JSON request body, it is attacker-controlled
input, and that gate is gone. Every endpoint taking a `matter_id` must
independently verify that the authenticated user owns it (or that it's the
shared `PHASE1_DEMO_MATTER_ID`, which is read-only) before touching the
retriever. `HybridRetriever.retrieve()` filters Chroma by `matter_id`
faithfully — it will happily and correctly retrieve from a matter the caller
has no right to see, because scoping is not the same thing as authorisation.
A FastAPI dependency that resolves and authorises the matter, used by every
matter-scoped route, is the right shape; the existing
`tests/test_matter_isolation.py` is the right place to extend coverage to
the HTTP layer.

## Push-to-talk, not duplex streaming

There are two ways to do voice. Only one of them is a reasonable first
version.

**Push-to-talk (recommended):** the lawyer holds a button or toggles the mic,
speaks, and releases. `MediaRecorder` produces one audio blob, which goes to
`/api/transcribe` as a normal multipart POST. Three plain HTTP requests per
question, all debuggable with `curl`, all with ordinary timeouts and retries.

**Continuous duplex streaming:** a WebSocket carrying audio frames up and
synthesised audio down, with voice-activity detection deciding when the
lawyer has stopped talking, barge-in handling so they can interrupt the
avatar mid-sentence, and partial transcripts rendering live. This is what
makes a voice assistant feel effortless, and it also adds connection
lifecycle management, backpressure, reconnection, and per-frame error
handling to a codebase whose hardest problems are currently retrieval
quality and citation faithfulness.

Build push-to-talk. It has a second, non-obvious advantage for this specific
product: a discrete "record" action creates a natural place to show the
transcript and let the lawyer correct it before the question is issued, which
doc 13 argues is a requirement rather than a nicety. Continuous streaming
removes that checkpoint by design.

## The latency budget

Numbers below are order-of-magnitude planning figures, not measurements —
instrument your own with the structured logging from
[06-production.md](06-production.md#observability) and replace them. The
shape is what matters.

| Stage | Rough cost | Notes |
|---|---|---|
| Upload audio blob | 50–200 ms | A few hundred KB for a short question |
| Whisper transcription | 0.5–3 s | Heavily model- and hardware-dependent (doc 13) |
| Hybrid retrieval | 50–300 ms | Local Chroma + in-memory BM25; first query per matter pays BM25 build |
| Claude generation | 2–6 s | `max_tokens=1024` in [chain.py](../src/policy_advisor/generation/chain.py) line 54 |
| Faithfulness check | < 10 ms | Deterministic string/locator check, no LLM |
| TTS synthesis | 0.3–2 s | Provider-dependent; streaming synthesis helps (doc 13) |
| **Total to first audio** | **~3–11 s** | |

Conversational speech tolerates roughly a second of silence before it feels
broken. You are an order of magnitude away from that and most of the gap is
Claude generation, which you cannot remove without giving up the grounded,
citation-checked answer that is the entire point of the product.

The honest resolutions are: fill the gap with avatar state that shows real
work happening rather than a spinner (doc 14), acknowledge the question
audibly the moment the transcript is confirmed, and set expectations in the
UI. Do not resolve it by streaming unchecked tokens into the speech
synthesiser — doc 13 covers why that specific shortcut is unavailable here.

### Voice mode applies to "Ask a question" only

The Streamlit app offers two modes ([app.py](../src/policy_advisor/app.py)
line 123). "Analyze a case" runs many checked reasoning steps per issue and
the UI already tells the user it takes minutes. That is not a voice
interaction, and its output — issues, per-side arguments with cited
authorities, confidence badges — is a structured document, not something to
listen to. Keep it in the Streamlit app, or give it a text-only screen in the
new client. Scope voice to the question-and-answer path.

## Configuration and secrets

New `.env` entries, following the existing pattern in
[config.py](../src/policy_advisor/config.py) (add them as typed fields on
`Settings`, and to `.env.example` with empty values):

```bash
# API server
API_PORT=8000
API_ALLOWED_ORIGINS=http://localhost:5173   # dev frontend origin, for CORS

# Speech (see docs/13)
STT_BACKEND=whisper-local
WHISPER_MODEL=openai/whisper-small
TTS_BACKEND=browser
TTS_API_KEY=                                 # only if using a cloud TTS provider
```

Two rules carry over from [06-production.md](06-production.md#secrets-handling)
and are worth restating because a browser client makes them easier to break:

- **No key of any kind reaches the browser.** Not `ANTHROPIC_API_KEY`, not a
  cloud TTS key. If the frontend calls a speech provider directly, that key
  is public — the fact that it's "only" a TTS key doesn't change that
  someone else can now spend your quota. Proxy through `/api/speak`.
- **`API_ALLOWED_ORIGINS` is an explicit allowlist**, never `*`. With
  cookie-based sessions, a permissive CORS policy plus credentialed requests
  is how you hand a third-party page the ability to ask questions as your
  logged-in user.

## Cost and quota: voice changes the economics

[11-web-search-and-usage-limits.md](11-web-search-and-usage-limits.md)
already traced that nothing in this repo caps usage, and
[06-production.md](06-production.md#reliability-extras-worth-planning-for)
flags the missing cost ceiling as unfinished work. Voice makes it urgent for
three compounding reasons:

1. Speaking a question is far cheaper for the user than typing one, so
   question volume per session goes up.
2. Every question now costs an STT call and a TTS call on top of the Claude
   call. If either is a paid cloud service, per-question cost roughly
   doubles in count of billable calls.
3. A held microphone button and a bad VAD threshold can generate requests
   the user didn't intend.

Build the per-user daily cap before the voice client is usable by anyone but
you, and surface it as a spoken and on-screen message ("you've used today's
quota") rather than a failed request. While you're there: the retry/backoff
wrapper currently protects only `_call_llm` in
[chain.py](../src/policy_advisor/generation/chain.py) line 59 —
`web_search.py`, `case_reasoning.py`, and `translate.py` still have none, as
doc 11 noted, and the new speech endpoints will need their own timeouts and
backoff for the same reason.

## Blocking prerequisite: the web fallback doesn't currently fire

Part of the goal for this interface is that it answers from official web
sources when the matter's own documents don't cover the question. That path
is implemented but effectively unreachable today, and
[11-web-search-and-usage-limits.md](11-web-search-and-usage-limits.md#1-why-the-web-search-checkbox-didnt-fire)
has the full trace: `HybridRetriever.retrieve()` applies no relevance floor
and returns the top `k` candidates however weak the match
([hybrid_retriever.py](../src/policy_advisor/retrieval/hybrid_retriever.py)
line 144), so for any matter with documents in it `retrieved` is essentially
never empty, so the `if not retrieved:` branch that triggers
`search_official_sources` never runs.

Two consequences for this work, which is why it belongs in the architecture
doc rather than being left to doc 11:

- The `source == "web"` response — and the avatar state, spoken preamble, and
  citation card built to handle it in docs 13 and 14 — is dead code until the
  relevance floor exists. Don't build the voice trust-signalling for a branch
  you can't exercise; fix the floor first so you can test it end to end.
- Widening the web-search allow-list (now rows in the source registry,
  [docs/24](24-source-registry.md); formerly a constant in `web_search.py`)
  beyond `nass.gov.ng` and `ndpc.gov.ng` is what makes this useful in
  practice, and that file's own docstring is right that it's a
  legal-judgment call. Decide the list with a qualified lawyer — which
  reporting bodies and court sites count as authoritative — and keep it as an
  allowlist. "Let the agent search the web" is a different product with a
  different liability profile, not a bigger version of this one.

Note also that the search tool is Claude's native `web_search_20250305`
server tool with server-side domain restriction, not a Google API. Nothing in
this interface changes that, and it's worth keeping in mind when describing
the feature to the lawyer: "official sources I'm allowed to check," not
"the web."

Next: [13-voice-pipeline.md](13-voice-pipeline.md)
