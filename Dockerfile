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

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY pyproject.toml ./
ENV PATH="/app/.venv/bin:$PATH"

# Index, eval artifacts, and lawyer credentials live under data/ - this
# directory is a mount point at runtime (see docker-compose.yml), not
# baked into the image.
RUN mkdir -p data && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501
CMD ["streamlit", "run", "src/policy_advisor/app.py", "--server.address=0.0.0.0"]
