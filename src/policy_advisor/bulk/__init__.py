"""Bulk analysis: typed, evidence-backed extraction over every document of
a matter, as a batch pipeline rather than a chat feature (ADR-001 decision
8; docs/30 slice P3-C; docs/33).

- `schemas.py`: what is extracted per document type (the contract ledger
  first, seeded from the plugin's obligations extractor).
- `evidence.py`: every extracted item must quote the document; the quote is
  found in the chunk it names, or the item is marked unverified.
- `jobs.py`: the `bulk_jobs` queue, the `extractions` and `findings` tables.
- `extract.py`: the extractor: model tiering, the token ceiling, the Batch
  API backend.
- `checks.py`: cross-document checks over the records.
"""
