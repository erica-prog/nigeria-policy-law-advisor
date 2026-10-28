"""Bulk analysis (docs/30 slice P3-C, AC-2 and AC-4): verified evidence,
tiering, the ceiling that pauses and resumes, the Batch API path with a fake
client, cross-document findings, and the agent's query tool. A fake model
returns canned records; nothing calls Claude."""

import numpy as np
import pytest
from pydantic import ValidationError

from policy_advisor import tenancy
from policy_advisor.bulk import checks, evidence
from policy_advisor.bulk.extract import (
    ExtractionParseError,
    Extractor,
    ModelResult,
    run_pending,
)
from policy_advisor.bulk.jobs import (
    BulkJobTable,
    list_extractions,
    list_findings,
    query_extractions,
)
from policy_advisor.bulk.schemas import (
    ContractExtraction,
    EvidenceRef,
    KeyDate,
    Obligation,
    Party,
    RiskClause,
)
from policy_advisor.config import get_settings
from policy_advisor.ingestion import matter_store

DIM = 384
CLAUSE_4 = "The Buyer shall pay each invoice within thirty days of delivery. Late payment attracts interest."
CLAUSE_9 = (
    "Either party may terminate on ninety days written notice delivered to the registered office."
)
CLAUSE_12 = "This agreement is governed by the laws of the Federal Republic of Nigeria."
CLAUSE_4B = "The Buyer shall pay each invoice within sixty days of delivery."


def _chunks(document, clauses):
    return [
        {
            "chunk_id": f"{document}::{locator.replace(' ', '')}",
            "text": text,
            "source_document": document,
            "locator": locator,
            "doc_type": "generic",
            "matter_id": "case-1",
            "jurisdiction": None,
            "page": 1,
        }
        for locator, text in clauses
    ]


@pytest.fixture
def matter(fresh_db):
    matter_store.set_matter_owner("case-1", "alice")
    docs = {
        "supply.docx": [("Clause 4", CLAUSE_4), ("Clause 9", CLAUSE_9), ("Clause 12", CLAUSE_12)],
        "amendment.docx": [("Clause 4", CLAUSE_4B), ("Clause 12", CLAUSE_12)],
    }
    for document, clauses in docs.items():
        records = _chunks(document, clauses)
        matter_store.replace_document_chunks(
            "case-1",
            document,
            records,
            np.ones((len(records), DIM), dtype=np.float32) / np.sqrt(DIM),
        )
    return "case-1"


def _record(document, *, days="thirty", bad_quote=False, risk=False):
    quote = CLAUSE_4 if document == "supply.docx" else CLAUSE_4B
    if bad_quote:
        quote = "The Buyer shall pay within seven days."
    return ContractExtraction(
        classification="complete executed agreement",
        parties=[
            Party(
                name="Acme Fabrication Ltd",
                role="Buyer",
                defined_as="the Buyer",
                evidence=[EvidenceRef(locator="Clause 4", quote="The Buyer shall pay")],
            )
        ],
        governing_law="the laws of the Federal Republic of Nigeria",
        governing_law_evidence=[
            EvidenceRef(
                locator="Clause 12", quote="governed by the laws of the Federal Republic of Nigeria"
            )
        ],
        key_dates=[
            KeyDate(
                label="Payment period",
                date_text=f"{days} days",
                evidence=[EvidenceRef(locator="Clause 4", quote=f"within {days} days of delivery")],
            )
        ],
        obligations=[
            Obligation(
                obligor="the Buyer",
                obligee="the Supplier",
                obligation="pay each invoice",
                deadline=f"within {days} days of delivery",
                evidence=[EvidenceRef(locator="Clause 4", quote=quote)],
            )
        ],
        risk_clauses=[
            RiskClause(
                kind="termination",
                summary="Either party may terminate on 90 days notice",
                evidence=[
                    EvidenceRef(locator="Clause 9", quote="terminate on ninety days written notice")
                ],
            )
        ]
        if risk
        else [],
    )


class FakeCaller:
    """Returns a canned record per (document, model); counts tokens as asked."""

    def __init__(self, records, tokens=(1000, 200)):
        self.records = records
        self.tokens = tokens
        self.calls = []

    def __call__(self, *, model, schema, document_text, api_key):
        self.calls.append((model, schema.schema_id))
        if schema.schema_id == "obligation_conflicts":
            return ModelResult(
                record=checks.ObligationConflicts(
                    conflicts=[
                        checks.ObligationConflict(
                            documents=["supply.docx", "amendment.docx"],
                            summary="Thirty versus sixty days for the same invoices.",
                            locators=["Clause 4"],
                        )
                    ]
                ),
                input_tokens=300,
                output_tokens=50,
            )
        key = "amendment.docx" if "sixty days" in document_text else "supply.docx"
        record = (
            self.records[(key, model)]
            if (key, model) in self.records
            else self.records[(key, None)]
        )
        return ModelResult(record=record, input_tokens=self.tokens[0], output_tokens=self.tokens[1])


def _settings(**overrides):
    base = {
        "bulk_triage_model": "triage-model",
        "bulk_escalation_model": "strong-model",
        "bulk_flag_confidence": 0.8,
        "rerank_enabled": False,
    }
    base.update(overrides)
    return get_settings().model_copy(update=base)


def _job(table, documents, ceiling=1_000_000, backend="sync"):
    return table.create(
        "case-1",
        schema_id="contract",
        schema_version=1,
        documents=documents,
        triage_model="triage-model",
        escalation_model="strong-model",
        ceiling_tokens=ceiling,
        backend=backend,
        submitted_by="alice",
    )


# --- evidence ---


def test_evidence_quotes_are_located_in_their_chunks_and_confidence_follows(matter):
    chunks = _chunks("supply.docx", [("Clause 4", CLAUSE_4), ("Clause 12", CLAUSE_12)])
    spans, confidence = evidence.verify_record(_record("supply.docx"), chunks)
    assert confidence == 1.0 and all(s.verified for s in spans)
    found = next(s for s in spans if s.path.startswith("obligations[0]"))
    assert CLAUSE_4[found.start : found.end] == CLAUSE_4
    spans, confidence = evidence.verify_record(_record("supply.docx", bad_quote=True), chunks)
    assert confidence == 0.75 and sum(1 for s in spans if not s.verified) == 1
    # Curly quotes and whitespace do not defeat the match.
    assert evidence._locate("“thirty  days”", "within “thirty days” of delivery") is not None
    assert evidence.verify_record(ContractExtraction(classification="excerpt"), chunks) == ([], 0.0)


# --- the job ---


def test_sync_job_extracts_every_document_escalates_flagged_ones_and_writes_findings(matter):
    table = BulkJobTable()
    caller = FakeCaller(
        {
            ("supply.docx", "triage-model"): _record(
                "supply.docx", risk=True
            ),  # risk clause: flagged for escalation
            ("supply.docx", "strong-model"): _record("supply.docx", risk=True),
            ("amendment.docx", "triage-model"): _record(
                "amendment.docx", days="sixty", bad_quote=True
            ),  # low confidence: flagged
            ("amendment.docx", "strong-model"): _record("amendment.docx", days="sixty"),
        }
    )
    job = _job(table, ["supply.docx", "amendment.docx"])
    final = Extractor(settings=_settings(), caller=caller, jobs=table).run(job)
    assert final.status == "done" and final.done_documents == 2
    models = [m for m, s in caller.calls if s == "contract"]
    assert models == ["triage-model", "triage-model", "strong-model", "strong-model"]
    assert [s for _, s in caller.calls].count("obligation_conflicts") == 1
    records = {r["source_document"]: r for r in list_extractions("case-1", schema_id="contract")}
    assert (
        records["amendment.docx"]["model"] == "strong-model"
        and float(records["amendment.docx"]["confidence"]) == 1.0
    )
    assert records["supply.docx"]["flagged"] is True  # risk clauses stay flagged for review
    assert final.spent_tokens == 4 * 1200 + 350
    kinds = {f["kind"] for f in list_findings("case-1")}
    assert kinds == {"date_conflict", "obligation_conflict"}
    date_conflict = next(f for f in list_findings("case-1") if f["kind"] == "date_conflict")
    assert (
        sorted(date_conflict["documents"]) == ["amendment.docx", "supply.docx"]
        and date_conflict["severity"] == "high"
    )


def test_ceiling_pauses_the_job_without_spending_and_resume_continues_it(matter):
    table = BulkJobTable()
    caller = FakeCaller(
        {
            ("supply.docx", None): _record("supply.docx"),
            ("amendment.docx", None): _record("amendment.docx", days="sixty"),
        },
        tokens=(5000, 1000),
    )
    job = _job(table, ["supply.docx", "amendment.docx"], ceiling=12_000)
    paused = Extractor(
        settings=_settings(bulk_escalation_model="triage-model"), caller=caller, jobs=table
    ).run(job)
    assert (
        paused.status == "paused_ceiling"
        and paused.done_documents == 1
        and paused.spent_tokens == 6000
    )
    assert "exceed the ceiling" in paused.error
    assert len(caller.calls) == 1, "the second document was never sent"
    with pytest.raises(ValueError):
        table.resume(job.job_id, 6000)
    resumed = table.resume(job.job_id, 100_000)
    assert resumed.status == "queued" and resumed.ceiling_tokens == 100_000
    with tenancy.tenant_context(tenancy.ALL_TENANTS):
        ran = run_pending(
            None,
            extractor_factory=lambda j: Extractor(
                settings=_settings(bulk_escalation_model="triage-model"), caller=caller, jobs=table
            ),
            jobs=table,
        )
    assert ran == 1
    final = table.get(job.job_id)
    assert final.status == "done" and final.done_documents == 2
    assert [r["source_document"] for r in list_extractions("case-1")] == [
        "amendment.docx",
        "supply.docx",
    ]


class FakeBatchClient:
    def __init__(self, records):
        self.records = records
        self.created = []
        self.polls = 0

    def create(self, requests):
        self.created.append(requests)
        return "batch-1"

    def status(self, batch_id):
        self.polls += 1
        return "ended" if self.polls > 1 else "in_progress"

    def results(self, batch_id):
        return [
            {
                "custom_id": name,
                "type": "succeeded",
                "input_tokens": 700,
                "output_tokens": 100,
                "tool_input": record.model_dump(),
            }
            for name, record in self.records.items()
        ] + [{"custom_id": "ghost.docx", "type": "errored", "error": "no such document"}]


def test_batch_backend_submits_once_polls_then_stores_results(matter):
    table = BulkJobTable()
    client = FakeBatchClient(
        {
            "supply.docx": _record("supply.docx"),
            "amendment.docx": _record("amendment.docx", days="sixty"),
        }
    )
    caller = FakeCaller({})
    extractor = Extractor(
        settings=_settings(bulk_escalation_model="triage-model"),
        caller=caller,
        jobs=table,
        batch_client_factory=lambda key: client,
    )
    job = _job(table, ["supply.docx", "amendment.docx"], backend="batch")
    first = extractor.run(job)
    assert first.status == "waiting_batch" and first.batch_id == "batch-1"
    [requests] = client.created
    assert [r["custom_id"] for r in requests] == ["supply.docx", "amendment.docx"]
    assert requests[0]["params"]["tool_choice"] == {
        "type": "tool",
        "name": "record_contract_extraction",
    }
    assert "input_schema" in requests[0]["params"]["tools"][0]
    second = extractor.run(table.get(job.job_id))
    assert second.status == "waiting_batch"  # still in progress on the first poll
    with tenancy.tenant_context(tenancy.ALL_TENANTS):
        assert table.claim_next().job_id == job.job_id  # a waiting job is claimable again
    third = extractor.run(table.get(job.job_id))
    assert (
        third.status == "done" and third.done_documents == 2 and third.spent_tokens == 2 * 800 + 350
    )
    assert {r["source_document"] for r in list_extractions("case-1")} == {
        "supply.docx",
        "amendment.docx",
    }


def test_unknown_document_text_fails_the_job_cleanly(matter):
    table = BulkJobTable()
    job = _job(table, ["missing.docx"])
    final = Extractor(settings=_settings(), caller=FakeCaller({}), jobs=table).run(job)
    assert final.status == "failed" and "no indexed text" in final.error


def test_a_list_field_sent_as_a_json_string_is_read_as_the_list():
    # The nightly run of 2026-10-28: the model sent governing_law_evidence as
    # a JSON-encoded string and the whole record was refused.
    record = ContractExtraction.model_validate(
        {
            "classification": "complete executed agreement",
            "governing_law": "the laws of the Federal Republic of Nigeria",
            "governing_law_evidence": '[{"locator": "Clause 8", "quote": "governed by the laws '
            'of the Federal Republic of Nigeria"}]',
            "parties": [
                {"name": "Acme", "evidence": '[{"locator": "Clause 1", "quote": "Acme Ltd"}]'}
            ],
        }
    )
    assert record.governing_law_evidence[0].locator == "Clause 8"
    assert record.parties[0].evidence[0].quote == "Acme Ltd"
    with pytest.raises(ValidationError):
        ContractExtraction.model_validate(
            {"classification": "x", "governing_law_evidence": "Clause 8 says Nigerian law"}
        )


def test_one_unparseable_document_is_skipped_named_and_charged_not_the_whole_job(matter):
    table = BulkJobTable()
    inner = FakeCaller(
        {
            ("supply.docx", None): _record("supply.docx"),
            ("amendment.docx", None): _record("amendment.docx", days="sixty"),
        }
    )

    def caller(*, model, schema, document_text, api_key):
        if schema.schema_id == "contract" and "sixty days" in document_text:
            raise ExtractionParseError(
                "the model returned no parseable record: governing_law_evidence",
                input_tokens=900,
                output_tokens=100,
            )
        return inner(model=model, schema=schema, document_text=document_text, api_key=api_key)

    job = _job(table, ["supply.docx", "amendment.docx"])
    final = Extractor(settings=_settings(), caller=caller, jobs=table).run(job)
    assert final.status == "done" and final.done_documents == 1
    assert final.error.startswith("1 of 2 documents not extracted: amendment.docx (")
    assert "governing_law_evidence" in final.error
    stored = {r["source_document"] for r in list_extractions("case-1", schema_id="contract")}
    assert stored == {"supply.docx"}
    assert final.spent_tokens == 1200 + 1000  # the failed call still counts toward the ceiling


def test_a_job_where_no_document_could_be_extracted_fails(matter):
    table = BulkJobTable()

    def caller(*, model, schema, document_text, api_key):
        raise ExtractionParseError("the model returned no parseable record", input_tokens=10)

    job = _job(table, ["supply.docx", "amendment.docx"])
    final = Extractor(settings=_settings(), caller=caller, jobs=table).run(job)
    assert final.status == "failed"
    assert final.error.startswith("2 of 2 documents not extracted")


# --- findings and the query tool ---


def test_deterministic_checks_find_party_date_term_and_law_differences():
    rows = [
        {
            "source_document": "a.docx",
            "record": {
                "parties": [
                    {
                        "name": "Acme Fabrication Limited",
                        "defined_as": "the Buyer",
                        "role": "Buyer",
                        "evidence": [{"locator": "Clause 1", "quote": "x"}],
                    }
                ],
                "key_dates": [{"label": "Term", "date_text": "2 years", "evidence": []}],
                "defined_terms": [
                    {
                        "term": "Business Day",
                        "definition": "a day banks open in Lagos",
                        "evidence": [],
                    }
                ],
                "governing_law": "Lagos State law",
            },
        },
        {
            "source_document": "b.docx",
            "record": {
                "parties": [
                    {
                        "name": "Acme Fabrication Ltd.",
                        "defined_as": "the Purchaser",
                        "role": "Buyer",
                        "evidence": [],
                    }
                ],
                "key_dates": [{"label": "Term", "date_text": "3 years", "evidence": []}],
                "defined_terms": [
                    {"term": "Business Day", "definition": "any weekday", "evidence": []}
                ],
                "governing_law": "English law",
            },
        },
    ]
    kinds = sorted(f["kind"] for f in checks.deterministic_findings(rows))
    assert kinds == [
        "date_conflict",
        "defined_term_conflict",
        "governing_law_varies",
        "party_mismatch",
    ]


def test_query_extractions_returns_citable_items(matter):
    table = BulkJobTable()
    caller = FakeCaller(
        {
            ("supply.docx", None): _record("supply.docx", risk=True),
            ("amendment.docx", None): _record("amendment.docx", days="sixty"),
        }
    )
    Extractor(
        settings=_settings(bulk_escalation_model="triage-model"), caller=caller, jobs=table
    ).run(_job(table, ["supply.docx", "amendment.docx"]))
    rows = query_extractions("case-1", field="obligations", contains="sixty")
    assert [r["document"] for r in rows] == ["amendment.docx"] and rows[0]["locators"] == [
        "Clause 4"
    ]
    assert len(query_extractions("case-1", field="risk_clauses")) == 1
    law = query_extractions("case-1", field="governing_law", contains="nigeria")
    assert len(law) == 2 and law[0]["locators"] == ["Clause 12"]
    assert (
        query_extractions("case-1", field="parties", source_document="supply.docx")[0]["item"][
            "role"
        ]
        == "Buyer"
    )
