FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

# pytesseract and pdf2image (ingestion/pdf_text.py) shell out to these
# system binaries - pip install alone is not enough for OCR/PDF parsing.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Node 20 LTS for the connector servers (docs/23 D4): the binary and its
# bundled npm copied from the official image, no package scripts run.
COPY --from=node:20-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src ./src
# MCP connector servers the API starts as child processes (docs/26).
COPY connectors ./connectors
# Static browser client and avatar frames served by the FastAPI app
# (docs/17-web-app-mvp.md).
COPY web ./web
COPY assets ./assets
COPY pyproject.toml ./
ENV PATH="/app/.venv/bin:$PATH"

# Lawyer credentials and the source PDFs live under data/ (matters, chunks and
# chat history live in Supabase) - this directory is a mount point at
# runtime (see docker-compose.yml), not baked into the image.
RUN mkdir -p data && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# The web app: FastAPI serving the JSON API and the browser client (docs/17).
CMD ["uvicorn", "policy_advisor.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
