# Nigeria: integrations

Connectors for Nigerian sources are built here (ADR-001 action 10, a TypeScript MCP
server), because no open connector exists. Planned order:

| Connector | Source | Access | Licence | Status |
|---|---|---|---|---|
| `ng-nigerialii` | NigeriaLII judgments and legislation | public website; respect robots and rate limits | free; attribution | planned |
| `ng-nass` | National Assembly Acts and bills | public website | free | planned (the `web_search` allow-list already covers `nass.gov.ng`) |
| `ng-ndpc` | NDPC Act, regulations, guidance | public website | free | planned (allow-listed today) |
| `ng-nis` | Nigeria Immigration Service policy and forms | public website | free | planned |
| `ng-courts` | Supreme Court, Court of Appeal, FHC, Lagos judiciary | public websites, PDF-heavy, OCR likely | free | planned; a curated mirror is the real asset |
| `ng-lawpavilion` | licensed judgments and citator | licensed API if available | licensed; no text storage | behind a feature flag |

Every connector returns the shared result schema (`source`, `source_url`, `retrieved_at`,
`coverage_warning`, `licence`, `access_status`) and is registered in the source registry
with a lawyer-approved `allowed` flag before the agent loop may call it.
