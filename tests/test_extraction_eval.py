"""The extraction eval (docs/30 slice P3-D, AC-3): the scorer rewards the
right things, the oracle scores 1.0 through the whole pipeline, and a
degraded answer loses points in the fields it got wrong."""

import copy

from eval.extraction.generate_set import RISK_OBLIGATIONS
from eval.extraction.score import FIELDS, Scores, agrees, norm, score_document
from eval.run_extraction_eval import OracleCaller, load_set, run


def _label():
    return load_set()[0]["labels"]


def test_set_is_large_and_labelled_with_evidence():
    documents = load_set()
    assert len(documents) >= 40
    assert len(FIELDS) == 8
    for document in documents[:10]:
        labels = document["labels"]
        assert labels["parties"] and labels["obligations"] and labels["governing_law"]
        locators = {c["locator"] for c in document["clauses"]}
        for obligation in labels["obligations"]:
            assert all(e["locator"] in locators for e in obligation["evidence"])


def test_labels_follow_the_schema_descriptions_not_a_narrower_reading():
    """Version 1 labelled one defined term, no termination or governing-law
    clause and no duty a risk clause imposes, so a model that followed the
    schema was scored wrong for it (docs/39 §9a, second nightly run)."""
    for document in load_set():
        labels = document["labels"]
        texts = {c["locator"]: c["text"] for c in document["clauses"]}
        kinds = {r["kind"] for r in labels["risk_clauses"]}
        assert {"termination", "governing law"} <= kinds
        assert {"Effective Date", "Business Day", "Term"} <= {
            t["term"] for t in labels["defined_terms"]
        }
        duty_locators = {e["locator"] for o in labels["obligations"] for e in o["evidence"]}
        for risk in labels["risk_clauses"]:
            if risk["kind"] in RISK_OBLIGATIONS:
                assert risk["evidence"][0]["locator"] in duty_locators, (document["name"], risk)
        for field in ("obligations", "defined_terms", "risk_clauses", "key_dates"):
            for item in labels[field]:
                for e in item["evidence"]:
                    assert e["quote"] in texts[e["locator"]], (document["name"], field, e)


def test_dates_and_deadlines_match_on_the_words_as_written_not_the_label():
    label = _label()
    predicted = copy.deepcopy(label)
    for date in predicted["key_dates"]:
        date["label"] = "Described differently"
        date["date_text"] = f"within {date['date_text']} as stated"
    for obligation in predicted["obligations"]:
        if obligation["deadline"]:
            obligation["deadline"] = f"{obligation['deadline']} of the Agreement"
    scores = Scores()
    score_document(scores, predicted, label)
    assert scores.fields["key_dates"].f1 == 1.0
    assert scores.fields["obligations"].f1 == 1.0

    assert agrees("within thirty Business Days of receipt", "thirty Business Days")
    assert not agrees("thirty days", "thirty Business Days")
    assert not agrees("thirty days", "one hundred and thirty-one days")
    assert agrees("", "") and not agrees("", "thirty days")

    wrong = copy.deepcopy(label)
    wrong["key_dates"] = [dict(d, date_text="1 January 1999") for d in wrong["key_dates"]]
    scores = Scores()
    score_document(scores, wrong, label)
    assert scores.fields["key_dates"].tp == 0
    assert scores.fields["key_dates"].fp == scores.fields["key_dates"].fn == len(label["key_dates"])


def test_governing_law_matches_on_the_law_named_not_the_sentence_around_it():
    """The schema asks for the governing law "as written". On the third
    nightly run the triage model returned the whole clause ("This Agreement is
    governed by the laws of England and Wales.") for 7 of 12 contracts and was
    scored wrong on every one (docs/39 §9a)."""
    label = _label()
    law = label["governing_law"]
    for written in (f"This Agreement is governed by {law}.", law.upper(), law.replace("the ", "")):
        scores = Scores()
        score_document(scores, dict(label, governing_law=written), label)
        assert scores.fields["governing_law"].f1 == 1.0, written
    for wrong in (
        "the laws of Mars",
        "",
        "the laws of Wales" if "England" in law else "the laws of England",
    ):
        scores = Scores()
        score_document(scores, dict(label, governing_law=wrong), label)
        field = scores.fields["governing_law"]
        assert field.tp == 0 and field.fn == 1 and field.fp == (1 if wrong else 0), wrong


def test_scorer_is_forgiving_of_articles_and_suffixes_and_strict_on_substance():
    label = _label()
    scores = Scores()
    predicted = copy.deepcopy(label)
    predicted["parties"][0]["name"] = predicted["parties"][0]["name"].replace("Ltd", "Limited")
    predicted["obligations"][0]["obligor"] = predicted["obligations"][0]["obligor"].replace(
        "the ", ""
    )
    score_document(scores, predicted, label)
    assert scores.mean_f1 == 1.0

    degraded = copy.deepcopy(label)
    degraded["parties"] = degraded["parties"][:1]  # dropped a party
    degraded["governing_law"] = "the laws of Mars"
    degraded["obligations"][0]["deadline"] = "within two days"  # wrong deadline: not a match
    degraded["obligations"][1]["evidence"] = [
        {"locator": "Clause 99", "quote": "x"}
    ]  # wrong locator
    scores = Scores()
    score_document(scores, degraded, label)
    assert scores.fields["parties"].recall == 0.5 and scores.fields["parties"].precision == 1.0
    assert scores.fields["governing_law"].f1 == 0.0
    wanted = len(label["obligations"])
    assert scores.fields["obligations"].tp == wanted - 1
    assert scores.fields["obligations"].fn == 1
    # Of the matched obligations, only the one moved to Clause 99 misses its locator.
    assert (
        scores.fields["obligation_evidence"].tp == wanted - 2
        and scores.fields["obligation_evidence"].fn == 1
    )
    assert scores.fields["risk_clauses"].f1 == 1.0 and scores.fields["classification"].f1 == 1.0
    assert 0 < scores.mean_f1 < 1
    assert norm("the Buyer, Ltd.") == "buyer"


def test_oracle_scores_one_through_the_pipeline(fresh_db):
    documents = load_set()[:6]
    scores, report = run(documents, oracle=True)
    assert report["job_status"] == "done" and report["missing_records"] == []
    assert scores.documents == 6 and scores.mean_f1 == 1.0
    assert report["mean_confidence"] == 1.0, "every labelled quote is found in its clause"
    assert OracleCaller(documents)._by_first_clause


def test_the_triage_only_run_reads_no_record_twice(fresh_db):
    """docs/39 D11 compares an escalated run with a triage-only one over the
    same contracts in the same database; each must score its own records."""
    documents = load_set()[:4]
    _, escalated = run(documents, oracle=True)
    assert escalated["escalated"] == 4, "every contract reports a risk clause"
    scores, triage = run(documents, oracle=True, escalate=False)
    assert triage["escalation_model"] == triage["triage_model"]
    assert triage["escalated"] == 0 and triage["missing_records"] == []
    assert {r["model"] for r in triage["records"].values()} == {triage["triage_model"]}
    assert scores.documents == 4 and scores.mean_f1 == 1.0
