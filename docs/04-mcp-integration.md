# 04 — MCP Integration

> **Since Phase 2 (docs/26):** the client side of this design is implemented in `src/policy_advisor/tools/`: one MCP client over stdio and streamable HTTP, a registry-backed tool policy, and the untrusted result wrapper. The connectors it reaches are the vendored `legal-research` server and the hosted CourtListener and GovInfo servers; the Nigeria connector follows in slice P2-D.

## What MCP is, briefly
MCP (Model Context Protocol) is a standard way to expose **tools** and
**context** to an LLM, independent of which client or orchestration
framework is calling it. Instead of writing one-off integration code every
time Claude needs to "search documents" or "look up a definition," you
implement an FastMCP **server** once, and any MCP-compatible client (including
Claude directly, or your LangChain app) can call it the same way.

Think of it as: a small API server, with a schema Claude understands well
enough to decide *when* and *how* to call it mid-conversation — similar in
spirit to function calling, but standardized so the tool definitions aren't
tied to one vendor's specific format.

## Why use it here instead of just stuffing retrieved context into the prompt
With a single upfront retrieval pass, you commit to one search before
knowing what the model will need. With retrieval **as an MCP tool**, the
model can:
- Issue an initial search, read the results, then issue a *follow-up* search
  (e.g., a rule was found, now find the case law that interprets it).
- Ask for a different jurisdiction's equivalent rule without you having
  hardcoded that follow-up path.
- Request document metadata (e.g., "what's the citation format / what page
  is this on") as a separate, cheaper call rather than bloating every
  retrieval response.

This matters for legal queries because they're often multi-hop: a real
question ("can a Lagos magistrate court matter be removed to the Federal
High Court?") needs reasoning across more than one document.

## Tools to expose (design, not final API)
| Tool | Purpose | Notes |
|---|---|---|
| `search_rules` | Hybrid search over statutory rules | Params: query, jurisdiction filter, top_k |
| `search_case_law` | Hybrid search over judgments | Params: query, top_k |
| `get_document_metadata` | Fetch citation info for a chunk/document ID | Cheap, no embedding call |
| `get_chunk_context` | Fetch surrounding chunks (prev/next) for a given chunk | For when retrieved text needs more surrounding context |

Keep tools **narrow and composable** rather than one mega "search everything"
tool — narrow tools are easier for the model to use correctly and easier for
you to test and evaluate in isolation.

## Server/client shape
- **MCP server** (likely TypeScript, given your stack) wraps your Python
  retrieval logic — either by calling out to a small Python service/HTTP
  endpoint, or by reimplementing the retrieval call in TS against the same
  vector store. Calling into the existing Python retrieval service is usually
  less duplication than maintaining two retrieval implementations.
- **MCP client**: Claude (via the Claude API/Agent SDK) or your LangChain
  orchestration layer, depending on which side drives the conversation loop.

## Security and reliability considerations
- **Scope what each tool can touch.** `search_rules` should only ever query
  the vector store, never the filesystem or an arbitrary URL — don't give a
  tool more reach than its name implies.
- **Validate tool inputs at the boundary.** A `top_k` of 10,000 or an
  unbounded query string is a resource-exhaustion risk, not just a
  correctness one. Validate types and bounds before the call reaches your
  retrieval logic (see Zod in [05-typescript-guide.md](05-typescript-guide.md)).
- **Treat tool results as untrusted-ish input back into the model.** Retrieved
  legal text is from your own corpus, so the prompt-injection risk is lower
  than with open web content, but still sanitize/template it rather than
  concatenating raw strings into a prompt blindly.
- **Timeouts and partial failure.** If `search_case_law` times out, the agent
  should be able to proceed with whatever it has (or say "couldn't search
  case law") rather than the whole turn failing.

Next: [05-typescript-guide.md](05-typescript-guide.md)
