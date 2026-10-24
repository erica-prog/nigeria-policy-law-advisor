# 07 — Demoing the Agent (Streamlit vs. TS/HTML/CSS)

> **Superseded (2026-10-24).** The Streamlit app was removed; the browser client in
> `web/`, served by FastAPI, is the only surface. See [17-web-app-mvp.md](17-web-app-mvp.md).
> Kept for the reasoning it records.

You have two reasonable paths to make the RAG agent visible and testable by
the lawyer, rather than living only in logs/terminal output. They serve
different purposes — pick based on what you're trying to prove right now.

## Where the UI sits in the architecture
Either option is a **thin client** on top of the pipeline already described
in [01-architecture.md](01-architecture.md). It does not replace any piece —
it just calls into the orchestration layer (LangChain → MCP tools → Claude)
and renders the result:

```
┌────────────┐      ┌─────────────────────┐      ┌──────────────────────┐
│  UI layer   │ ───▶ │  Orchestration API    │ ───▶ │  Retriever + Claude    │
│ (Streamlit  │      │  (FastAPI / Express)  │      │  (existing pipeline)   │
│  or TS web) │ ◀─── │                       │ ◀─── │                       │
└────────────┘      └─────────────────────┘      └──────────────────────┘
```

The UI should never call Claude or the vector store directly with embedded
API keys — it talks to *your own* backend endpoint, which holds the secrets
server-side. This is true even for a throwaway demo: a key shipped to a
browser is a leaked key, full stop.

## Option A: Streamlit (Python) — fastest path to "show it working"
Best for: validating retrieval/answer quality with the lawyer before
investing in a real frontend. It's a Python script that renders a chat-like
UI — no separate frontend build step, no TS needed.

**What it's good at here:**
- A chat input + response loop is a built-in pattern (`st.chat_message`,
  `st.chat_input`), so you get a usable interface in well under 100 lines.
- It runs in the same Python process as your retrieval pipeline — no API
  layer required for a local demo, which means fewer moving parts while
  you're still validating retrieval quality.
- Easy to show **citations and sources** transparently: render the answer,
  then an expandable section per cited chunk showing the exact rule/judgment
  text and its source document — this directly supports the "verifiable,
  not just plausible" requirement from [00-overview.md](00-overview.md).

**What it's not good at:**
- Not a production frontend — session/auth handling, custom styling, and
  multi-user concurrency are all limited.
- Couples the demo tightly to Python; if your MCP server ends up in
  TypeScript, Streamlit can't call it directly without going through an HTTP
  layer anyway — at which point you've already built the API boundary the
  TS option also needs.

**Demo-specific things still worth doing properly:**
- Load secrets via `.env`/`python-dotenv` (per
  [02-environment-setup.md](02-environment-setup.md)) — don't hardcode keys
  in the Streamlit script because "it's just a demo."
- Show retrieval failure honestly (e.g., "no matching rule found") instead
  of letting Claude improvise — the demo should model the same faithfulness
  discipline as production, otherwise it teaches the wrong lesson about what
  "works" looks like.
- Surface latency/cost per query in the UI (a small caption under the
  answer) — cheap to add, and it's the kind of observability signal from
  [06-production.md](06-production.md) you want visible early, not bolted on
  later.

## Option B: TypeScript + HTML/CSS — closer to the real product
Best for: once retrieval/answer quality is validated and you want something. It should have a clean `white` background.
that looks and behaves like the eventual product (or if the MCP server is
already TypeScript and you want one language end-to-end on that side).

**Shape of it:**
- A small backend (Express or Fastify, TS) exposes one endpoint, e.g.
  `POST /ask`, that internally calls your MCP-based orchestration and
  returns `{ answer, citations, retrievedChunks }`.
- A frontend — can be as plain as static HTML/CSS with a `<script>` calling
  `fetch()`, or a lightweight framework (React/Next.js) if you want
  componentized chat history. For a first pass, plain HTML/CSS + `fetch` is
  enough and keeps the TS surface area small while you're still learning it.

**Minimal shape, illustrative only (not a full implementation):**
```typescript
// backend: apps/api/src/server.ts
app.post("/ask", async (req, res) => {
  const { question } = AskSchema.parse(req.body); // Zod-validated input — see 05-typescript-guide.md
  const result = await runAgent(question);          // calls your LangChain/MCP orchestration
  res.json(result);
});
```
```html
<!-- frontend: index.html -->
<script type="module">
  const res = await fetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: document.querySelector("#q").value }),
  });
  const { answer, citations } = await res.json();
  // render answer + citations into the page
</script>
```
This mirrors the async/fetch and Zod-validation concepts already covered in
[05-typescript-guide.md](05-typescript-guide.md) — the new piece here is just
that the "caller" is now a browser instead of an MCP client, so the same
input-validation discipline applies at the HTTP boundary.

**What this buys you over Streamlit:**
- A real client/server boundary, which is what you'll need in production
  anyway — the demo doubles as the first version of the actual API.
- Full control over how citations/sources are displayed (e.g., inline
  footnote-style links into the source PDF page), which matters for a
  lawyer's trust in the tool more than visual polish does.

**What it costs you:**
- More moving parts (two processes, CORS between them in dev, a build step)
  before you see anything on screen — slower to first demo than Streamlit.

## Recommendation
Use **Streamlit first** to validate retrieval and answer quality with
real questions from the lawyer — it's disposable and fast. Once you're
confident in retrieval/faithfulness (per the eval approach in
[06-production.md](06-production.md)), build the **TS/HTML backend+frontend**
as the real product surface, reusing the same orchestration layer either
demo called into. Don't design the Streamlit demo to be thrown away
carelessly, though — the citation-rendering and failure-honesty patterns you
build there are exactly what the TS frontend needs to replicate.

## Update: Option B, made concrete (and partly superseded)

The spoken cartoon-advisor interface described in
[12-voice-web-app-architecture.md](12-voice-web-app-architecture.md) is
Option B built out for real — same client/server boundary, same
"UI never holds a key" rule, same citation-rendering discipline. Voice is what
forces the move: Streamlit can't capture a microphone, control audio playback,
or animate an avatar across reruns, so the API boundary stops being optional.

Two corrections to this document in light of the code that now exists:

- **The Express/Fastify backend recommended above is superseded by FastAPI.**
  The whole orchestration layer (`RAGChain`, `HybridRetriever`,
  `check_faithfulness`, Chroma, the sentence-transformers model) is Python, so
  a TS backend would have to call a second Python service to do anything. TS
  moves entirely to the browser client, where it still does real work.
  Reasoning in doc 12.
- **The Streamlit app is not disposable.** It remains the better surface for
  document ingestion, matter management, and the multi-minute case analysis,
  and it's the natural internal/debug view once the voice client exists. Keep
  both.
