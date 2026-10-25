"""The extraction eval (docs/30 slice P3-D, AC-3): the scorer rewards the
right things, the oracle scores 1.0 through the whole pipeline, and a
degraded answer loses points in the fields it got wrong."""

import copy

from eval.extraction.score import FIELDS, Scores, norm, score_document
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
    assert scores.fields["obligations"].tp == 1 and scores.fields["obligations"].fn == 1
    assert (
        scores.fields["obligation_evidence"].tp == 0
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
