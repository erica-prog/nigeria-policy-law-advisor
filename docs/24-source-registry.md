# 24 — The source registry

**Status:** implemented (docs/23 slice P2-A). **Decision:** ADR-001 decision 5.

## Why a table and not a constant

Until this slice, the only external source the advisor could reach was a web
search restricted to two Nigerian domains written into `web_search.py`. Changing
that list meant a code change and a deploy, nobody recorded who had decided the
list was safe, and nothing tied a retrieved passage back to the terms under which
it could be stored. Phase 2 adds connectors for United States and Nigerian
primary law, each with its own licence, so the question "may the advisor use this
source, and what may it keep?" needs one answer in one place.

The registry is the `sources` table (migration `0007`). One row per source.
Nothing else in the code decides whether a source may be used.

## What a row records

| Column | Meaning |
|---|---|
| `source_id` | Stable slug, for example `us-ecfr`. Chunks ingested from the source carry it in `chunks.source_id`. |
| `country`, `tier`, `trust_kind` | The same vocabularies the chunk metadata uses (taxonomy countries; `matter`/`firm`/`authority`; `evidence`/`firm`/`authority`/`web`). |
| `connector` | Which code path reaches it: `web_search`, a local MCP server name such as `legal-research` or `ng-sources`, a hosted MCP server such as `courtlistener`, or `library` for text committed to the repository. |
| `licence` | JSON: `terms_url`, `may_store_text`, `may_store_snippets`, `attribution`. The ingestion and agent code read these before storing anything. |
| `freshness` | JSON: how currency is checked and when it last was. |
| `config` | Connector-specific JSON: `domains` for web search, `url` and `credential_env` for a hosted server, `denied_tool_patterns` for tools no playbook may call. |
| `allowed`, `allowed_by`, `allowed_at` | A lawyer's sign-off. Off by default for every source except the Phase 1 web allow-list, which was already in production. |

Credentials never go in `config`; it names the environment variable that holds
them (docs/23 D7), and the server reads it at call time.

## How the code uses it

- `policy_advisor.sources.web_search_domains()` is the union of `config.domains`
  over the allowed `web_search` rows. `search_official_sources()` reads it on
  every call. **No allowed row means no search**: the function returns "not
  found" without calling Anthropic. An empty registry fails closed.
- `policy_advisor.sources.require_allowed(source_id)` raises `UnknownSource` or
  `SourceNotAllowed`. The agent loop (slice P2-E) calls it before every
  connector tool; it raises rather than returning a flag so a caller cannot
  forget to check.
- `seed()` inserts the packaged rows and refreshes descriptive columns of
  existing ones. It never changes `allowed`: re-seeding after a lawyer withdrew a
  source does not switch it back on.

## Operating it

```bash
uv run python -m scripts.manage_sources seed
```

```bash
uv run python -m scripts.manage_sources list --not-allowed
```

```bash
uv run python -m scripts.manage_sources allow us-ecfr --by "A. Lawyer"
```

`show <source_id>` prints a row as JSON; `disallow` withdraws a sign-off and
records who did it. `scripts.check_database` reports how many sources are
registered and allowed, and warns when the registry is empty.

Migration 0007 itself inserts the `web-official-ng` row, so an existing
deployment keeps searching `nass.gov.ng` and `ndpc.gov.ng` without running the
seed. The packaged seed (`policy_advisor/sources/data/seed_sources.json`) adds
the United States connectors adopted from the `legal-ai-skills` plugin, the USCIS
Policy Manual library, and the two Nigerian sources the TypeScript connector will
serve. All of them wait for a sign-off.

## Production notes

- **Who signs off.** `--by` is required and stored. Treat it like any other
  audit field: it should name a person, not a role.
- **Licence before storage.** A connector may return full text the registry says
  may not be stored (`may_store_text: false`). Slices P2-C and P2-E keep such text
  in the request only and store a snippet and a URL. The CourtListener row is
  marked that way until its terms are read.
- **Tests.** `tests/test_sources.py` covers seeding, sign-off, the gate and the
  web-search domains. The `fresh_db` fixture does not truncate `sources`; the
  `registry` fixture in that file empties it and restores the seed afterwards.
- **What is still open.** A lawyer has not reviewed the seed
  (`reviewed_by_lawyer: false`), the same gap the taxonomy has. The
  `tool_prefix` column is empty until the plugin server is vendored (P2-C) and
  its tool names are known.
