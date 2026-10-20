# CLAUDE.md

Project memory for the **Law/Policy Advisor RAG Agent**. Read this before
responding to any request about this project.

## Goal
Build a production-ready RAG agent that acts as a **law and policy advisor**.
The end user is a practicing lawyer (non-technical). The agent must be robust
enough for real production use, not a demo.

## Stack
- **Claude API** — core reasoning and generation
- **MCP** — tool / context integration
- **LangChain** — orchestration
- **Hugging Face** — embeddings and models
- **Python** + **TypeScript**
- **uv** — Python environment and dependency management
- **.env** — all secrets and config
- **JSON** — data interchange / config where relevant

## Audience & teaching notes
- The developer (me) is **new to TypeScript but willing to learn** — explain
  TS concepts as they come up; don't assume prior knowledge.
- Comfortable enough with Python.

## Response rules
- **Explain, don't build.** Provide guidance on *how* to build the project,
  not a finished implementation, unless I explicitly ask for code.
- **Deliver the guidance as created markdown files** (one per topic), not as
  chat replies. Save them into a `docs/` folder.
- Suggested files to produce: `00-overview.md`, `01-architecture.md`,
  `02-environment-setup.md` (uv, .env), `03-data-and-retrieval.md`,
  `04-mcp-integration.md`, `05-typescript-guide.md`, `06-production.md`
  (eval, error handling, observability, secrets). Add or split files as the
  project grows.
- Lead with **production concerns**: retrieval quality, evaluation, secrets
  handling, error handling, observability, and reliability.
- When TypeScript appears, briefly explain the unfamiliar parts inside the doc.

## Out of scope (unless I ask)
- Generating the full codebase
- Deployment infra choices beyond what's needed to explain the architecture