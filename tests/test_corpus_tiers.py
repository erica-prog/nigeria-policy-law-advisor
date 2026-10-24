"""Corpus tiers and the chunk metadata contract (docs/22 slice P1-B, AC-6)."""

from pathlib import Path

import numpy as np

from policy_advisor.db import connect, transaction
from policy_advisor.ingestion import matter_store
from policy_advisor.ingestion.chunk import Chunk, _enrich
from policy_advisor.retrieval.vector_store import chunk_to_record

MIGRATION = Path("src/policy_advisor/migrations/0003_corpus_tiers.sql")
DIMENSIONS = 384


def _vectors(n: int) -> np.ndarray:
    return np.eye(n, DIMENSIONS, dtype=np.float32)


def _legacy_row(conn, matter_id, chunk_id, locator, doc_type, jurisdiction):
    """A row as migration 0001 wrote it: no tier, no scheme, flat jurisdiction."""
    conn.execute(
        "INSERT INTO chunks (matter_id, chunk_id, source_document, doc_type, jurisdiction, locator, "
        "page, text, language, embedding) VALUES (%s, %s, %s, %s, %s, %s, 1, %s, 'en', %s)",
        (matter_id, chunk_id, "doc.pdf", doc_type, jurisdiction, locator, "text", _vectors(1)[0]),
    )


def test_backfill_classifies_legacy_rows_and_is_idempotent(fresh_db):
    with connect() as conn, transaction(conn):
        conn.execute("INSERT INTO matters (matter_id) VALUES ('phase1-demo'), ('case-1')")
        _legacy_row(conn, "phase1-demo", "r1", "Order 1 Rule 1", "statute", "federal")
        _legacy_row(conn, "phase1-demo", "p1", "Paragraph 11", "judgment", "federal")
        _legacy_row(conn, "case-1", "s1", "Section 3", "generic", "lagos")
    backfill = MIGRATION.read_text(encoding="utf-8").split("-- BACKFILL", 1)[1]
    for _ in range(2):  # re-running must change nothing the second time
        with connect() as conn, transaction(conn):
            conn.execute(backfill)

    assert matter_store.get_matter_tier("phase1-demo") == "authority"
    assert matter_store.get_matter_tier("case-1") == "matter"
    rows = {r["chunk_id"]: r for r in matter_store.load_matter_chunks("phase1-demo")}
    rows.update({r["chunk_id"]: r for r in matter_store.load_matter_chunks("case-1")})
    assert rows["r1"]["tier"] == "authority" and rows["r1"]["trust_kind"] == "authority"
    assert rows["r1"]["locator_scheme"] == "ng_rules"
    assert rows["r1"]["jurisdiction"] == "ng-federal" and rows["r1"]["country"] == "NG"
    assert rows["r1"]["jurisdiction_level"] == "federal" and rows["r1"]["region"] == ""
    assert (
        rows["p1"]["locator_scheme"] == "judgment_para" and rows["p1"]["trust_kind"] == "authority"
    )
    assert rows["s1"]["tier"] == "matter" and rows["s1"]["trust_kind"] == "evidence"
    assert rows["s1"]["locator_scheme"] == "section"
    assert rows["s1"]["jurisdiction"] == "ng-lagos" and rows["s1"]["region"] == "Lagos"


def test_new_chunks_carry_the_contract_and_round_trip(fresh_db):
    chunk = Chunk(
        chunk_id="c1",
        text="A defence shall be filed within 30 days.",
        source_document="rules.pdf",
        doc_type="statute",
        matter_id="lib",
        jurisdiction="ng-lagos",
        locator="Order 5 Rule 3",
        page=2,
    )
    _enrich([chunk], tier="authority")
    assert (chunk.trust_kind, chunk.locator_scheme, chunk.country, chunk.region) == (
        "authority",
        "ng_rules",
        "NG",
        "Lagos",
    )
    matter_store.set_matter_tier("lib", "authority")
    matter_store.replace_document_chunks("lib", "rules.pdf", [chunk_to_record(chunk)], _vectors(1))

    (stored,) = matter_store.load_matter_chunks("lib")
    assert stored["tier"] == "authority" and stored["trust_kind"] == "authority"
    assert stored["locator_scheme"] == "ng_rules" and stored["jurisdiction_level"] == "state"
    assert stored["effective_date"] == "" and stored["licence"] == ""


def test_a_chunk_takes_its_matter_tier_whatever_the_record_claims(fresh_db):
    record = {"chunk_id": "x", "source_document": "d.pdf", "text": "t", "tier": "authority"}
    matter_store.replace_document_chunks("case-9", "d.pdf", [record], _vectors(1))
    (stored,) = matter_store.load_matter_chunks("case-9")
    assert stored["tier"] == "matter"
    assert stored["trust_kind"] == "evidence"


def test_set_matter_tier_rejects_unknown_tiers_and_moves_existing_chunks(fresh_db):
    import pytest

    matter_store.replace_document_chunks(
        "kb",
        "memo.docx",
        [{"chunk_id": "m", "source_document": "memo.docx", "text": "t"}],
        _vectors(1),
    )
    with pytest.raises(ValueError):
        matter_store.set_matter_tier("kb", "secret")
    matter_store.set_matter_tier("kb", "firm")
    (stored,) = matter_store.load_matter_chunks("kb")
    assert stored["tier"] == "firm"
