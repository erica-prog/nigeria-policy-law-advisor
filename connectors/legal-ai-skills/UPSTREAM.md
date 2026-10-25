# Vendored from `rohasnagpal/legal-ai-skills`

Files in this directory are copied, unmodified, from the MIT-licensed plugin
(ADR-001 "adopt" decision; docs/23 D3). They are never edited in place: a
change belongs upstream, and a refresh is a new copy at a new pinned commit.

| Field | Value |
|---|---|
| Repository | https://github.com/rohasnagpal/legal-ai-skills |
| Commit | `ad5df1934e981f9aa1c2c163da02590d8ae0504c` (main, 2026-10-10) |
| Plugin version | 4.1.1 (`VERSION` in the server) |
| Licence | MIT, `LICENSE` here (copyright Rohas Nagpal) |
| Vendored on | 2026-10-24 |

| File | Upstream path | SHA-256 |
|---|---|---|
| `legal-research-server.mjs` | `plugins/legal-ai-skills/mcp/legal-research-server.mjs` | `3501e6d554109c5f68e67856d107752dfaeed370101afff17900707031f69900` |
| `legal-research-server.test.mjs` | `plugins/legal-ai-skills/mcp/legal-research-server.test.mjs` | `1ac6f5e8dfe859e1dfb5824f0299a627ea56d7809bbec9a02631af18e8730feb` |
| `LICENSE` | `plugins/legal-ai-skills/LICENSE` | (MIT text) |

## What the server is

A dependency-free Node script speaking MCP over stdio (newline-delimited
JSON-RPC): `initialize`, `ping`, `tools/list`, `tools/call`. Twelve read-only
tools over official sources: the US Federal Register, Regulations.gov (needs
`REGULATIONS_GOV_API_KEY`), the eCFR, and four United Kingdom sources. Every
result carries `source`, `source_url`, `retrieved_at` and `coverage_warning`;
errors come back as `isError: true` with a plain message. It never writes
anywhere. Run it with `node legal-research-server.mjs`; no `npm install`.

Only the United States tools have registry rows (docs/24): `us-ecfr`,
`us-federal-register`, `us-regulations-gov`, each naming its tools in
`config.tools`. The UK tools have no row, so the policy layer refuses them.

## Running its tests

```bash
node --test connectors/legal-ai-skills/legal-research-server.test.mjs
```

Known upstream quirk: the handshake test spawns the server by
`new URL(...).pathname`, which is percent-encoded, so it fails from a checkout
whose path contains spaces. CI checks out to a path without spaces.

## Refreshing

1. Note the new upstream commit and read its diff for these files.
2. Copy the files again, update the table above (commit, date, hashes).
3. Run the server's tests and `tests/test_connectors.py`.
4. If a tool was added, renamed or removed, update the `config.tools` lists in
   `policy_advisor/sources/data/seed_sources.json` and re-run the seed.
