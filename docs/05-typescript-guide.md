# 05 — TypeScript Guide (for a Python developer)

You'll most likely hit TypeScript when writing the MCP server. This is a
map of the concepts you need, framed against what you already know in
Python — not a full language tutorial.

## The core idea: types exist only at compile time
Python has type *hints* that are optional and unchecked at runtime (unless
you add a library like `pydantic`). TypeScript types are **checked at
compile time, then erased** — the JS that actually runs has no type
information at all. This has a direct consequence for production code:

> A TS type can tell you "this MCP tool call's `query` param is a `string`"
> at compile time, but it does **nothing** to stop bad data arriving at
> *runtime* from an external client. For anything crossing a boundary
> (network input, MCP tool calls, env vars), use a runtime validator —
> commonly **Zod** — not just a TS type. This is the TS equivalent of why
> you'd use `pydantic` in Python instead of trusting a type hint alone.

## Type basics
```typescript
// like a Python TypedDict / dataclass
interface SearchRulesParams {
  query: string;
  jurisdiction?: "federal" | "lagos";   // optional field; union of literal values
  topK: number;
}

function searchRules(params: SearchRulesParams): Promise<Chunk[]> {
  // ...
}
```
- `interface` (or `type`) ≈ Python's `TypedDict`/`dataclass`, but compile-time
  only.
- `?` after a field name = optional, like a Python field with a default of
  `None`.
- `string | number` is a **union type** — "one of these." Closest Python
  analogue: `str | int` type hints, but here it's enforced by the compiler.
- `Promise<T>` ≈ Python's `Awaitable`/coroutine return — "a value of type
  `T` that resolves later."

## Async/await — same idea as Python, different runtime model
```typescript
async function getChunk(id: string): Promise<Chunk> {
  const result = await fetchFromStore(id);
  return result;
}
```
- `async`/`await` reads almost identically to Python.
- Key difference: Node is **single-threaded with an event loop** (same
  concurrency model as Python's `asyncio`, not threads). `await` yields
  control back to the event loop, it doesn't block the thread — so you don't
  need an executor/thread-pool dance for I/O-bound work the way you
  sometimes do mixing sync and async Python code.
- An `async` function **always** returns a `Promise`, even if you `return`
  a plain value — TS wraps it automatically.

## Error handling
```typescript
try {
  const chunks = await searchRules(params);
} catch (err) {
  // err is `unknown` by default in strict TS — narrow it before using it
  if (err instanceof Error) {
    logger.error({ message: err.message }, "search_rules failed");
  }
  throw err; // or return a typed error response
}
```
- `catch (err)` gives you `unknown`, not `Error`, under `strict` mode —
  unlike Python where the caught exception is always at least an
  `Exception`. You must narrow the type (`instanceof Error`) before
  accessing `.message`.
- Avoid `catch {}` (swallowing errors silently) — same rule as avoiding bare
  `except:` in Python, for the same reason: silent failures are the hardest
  bugs to find later.
- For tool-call-style code, prefer returning a typed result (`{ ok: true,
  data } | { ok: false, error }`) over throwing, when the caller (Claude, via
  MCP) needs to see *why* a call failed rather than the whole turn erroring
  out.

## Modules
- `import { searchRules } from "./retrieval.js"` ≈ Python's
  `from .retrieval import search_rules`.
- Note the `.js` extension in the import even though the source file is
  `.ts` — under `"module": "NodeNext"` (recommended in
  [02-environment-setup.md](02-environment-setup.md)), TS expects the
  extension the *compiled* file will have, which trips up most people coming
  from Python.

## Zod (runtime validation) — your `pydantic` equivalent
```typescript
import { z } from "zod";

const SearchRulesSchema = z.object({
  query: z.string().min(1).max(500),
  jurisdiction: z.enum(["federal", "lagos"]).optional(),
  topK: z.number().int().min(1).max(20),
});

type SearchRulesParams = z.infer<typeof SearchRulesSchema>; // derive the TS type from the schema
```
Validate every MCP tool call's input through a schema like this before it
touches your retrieval logic — this is your defense against the
resource-exhaustion and malformed-input risks mentioned in
[04-mcp-integration.md](04-mcp-integration.md).

Next: [06-production.md](06-production.md)
