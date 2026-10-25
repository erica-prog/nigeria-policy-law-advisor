# 27 — The Nigeria connector

**Status:** implemented (docs/23 slice P2-D). **Decisions:** ADR-001 action 10 and
decision 10 (TypeScript for the connector); docs/23 R1.

## What changed from the plan, and why

The plan named NigeriaLII and the National Assembly as the first two sources.
Reading their sites before writing a line of code (risk R1) changed that:

- **NigeriaLII is excluded.** Its `robots.txt` carries
  `Content-Signal: ai-input=no` (an express reservation against feeding content
  to AI models, including retrieval-augmented generation) and disallows
  `/judgments/`; its terms of use prohibit scraping and bulk download and licence
  content under CC BY-NC 4.0, which a commercial product cannot satisfy. No
  connector reaches it. Its registry row records the finding with `allowed: false`
  and `may_store_snippets: false`. The route back is their API and a licence, by
  email, which a lawyer should handle.
- **The National Assembly portal has no data endpoint.** Its bills and documents
  listings are rendered by script from a data table whose source was not
  discoverable; its search form errors. It stays reachable through web search
  (`web-official-ng`), and its row says so.

What is reachable, properly, is the set of official sites that publish through
WordPress. WordPress exposes its own REST API (`/wp-json/wp/v2/`), which is the
site's machine interface, not a scrape of its pages, and all three sites' robots
files allow it. The connector reads three:

| Registry row | Site | What it holds |
|---|---|---|
| `ng-ndpc` | ndpc.gov.ng | Nigeria Data Protection Commission: the NDPA, guidance, directives, news |
| `ng-nis` | immigration.gov.ng | Nigeria Immigration Service: visa categories, residence permits, requirements |
| `ng-fhc` | fhc.gov.ng | Federal High Court: rules, practice directions, notices |

All three are off until a lawyer signs them off (`manage_sources allow`).

## The server

`connectors/ng-sources/` is a TypeScript MCP server on the official
`@modelcontextprotocol/sdk` with Zod schemas. Three tools, matching ADR-001's
`search`, `fetch`, `cite`:

| Tool | Does | Returns |
|---|---|---|
| `search_ng_official` | Site search across one or all three sites | hits: site, id, kind (`page` is guidance, `post` is news), title, url |
| `fetch_ng_official` | Reads one page or post by its URL, host-checked against the allow-list | title, dates, text (bounded at 12,000 characters), truncated flag |
| `cite_ng_official` | Formats a citation for a page | one string, OSCOLA-style, with the accessed date |

Every result carries the envelope the US connectors use: `source`, `source_url`,
`retrieved_at`, `coverage_warning`, `licence`. The Python side (docs/26) treats
it exactly like the vendored US server: registry gate, deny patterns, budget,
untrusted wrapper.

Production details:

- **Allow-listed hosts only.** `fetch_ng_official` refuses any URL whose host is
  not one of the three, so a URL the model found elsewhere cannot turn the
  connector into a general fetcher.
- **Bounded and timed.** Twenty-second request timeout; 12,000-character text
  cap; `limit` capped at 20.
- **No credentials.** The sites are public; the registry rows name none.
- **Fixtures, not network, in tests.** The reader takes a `fetch` implementation,
  and Vitest hands it recorded JSON from the sites' REST responses
  (`test/fixtures/`), trimmed. Live reads are a manual smoke test.

## Building and running

```bash
cd connectors/ng-sources && npm ci && npm run typecheck && npm test && npm run build
```

The Dockerfile builds it in a Node stage and copies `dist/` and the production
`node_modules` into the image; the registry row's `config.server` points at
`ng-sources/dist/server.js`, which the Python client starts over stdio.
`tests/test_connectors.py` starts the built server and lists its tools; the test
is skipped until `dist/` exists.

## What a lawyer should confirm

- That reading guidance pages from these three sites and storing snippets with
  URLs is acceptable use (the rows say `may_store_text: false` until then).
- The NigeriaLII reading above.
- Which further official sites to add: `supremecourt.gov.ng` is not WordPress and
  needs its own reader; state judiciaries vary.

## TypeScript, as it appears here

See [docs/05](05-typescript-guide.md), which this slice extends with the
concepts the server uses: ES modules and `NodeNext` resolution (the `.js` in
import paths), `as const` and literal types, Zod schemas as both validator and
type, `typeof fetch` for injectable dependencies, and `async/await`.
