"""Pick RETRIEVAL_MAX_DISTANCE empirically instead of guessing it.

`HybridRetriever` gates a query out entirely when the best cosine distance in
the matter is worse than this threshold, which is what makes "nothing relevant"
representable and, in turn, what lets the official-sources fallback in chain.py
fire at all. The threshold has to sit between two populations:

  positives - golden-set questions the corpus does answer; gating any of these
              out is a regression, because the lawyer gets a web answer (or
              nothing) when their own documents held the answer.
  negatives - golden-set questions marked expect_refusal, which the corpus
              genuinely cannot answer; failing to gate these is the bug
              docs/11 traced, where retrieval returns the eight least-bad
              chunks and the fallback never runs.

Re-run after changing EMBEDDING_MODEL, the distance space, or chunking - all
three move the two distributions, and a threshold calibrated under one
combination means nothing under another.

Usage:
  uv run python -m eval.calibrate_relevance_floor
  uv run python -m eval.calibrate_relevance_floor --top-k 8
"""

import argparse
import json
from pathlib import Path

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"

# Leave the retriever's own gate out of the way: we need the distances it would
# have judged, not its verdict. 2.0 is the maximum possible cosine distance.
GATE_DISABLED = 2.0

# Below this, the gap between the hardest answerable question and the easiest
# unanswerable one is narrower than the variation you would expect from simply
# rephrasing a question, so a single threshold is not measuring anything real.
MIN_TRUSTWORTHY_MARGIN = 0.05

# How far past the worst off-corpus question the hard-reject threshold sits.
# Covers matters whose text is nothing like this corpus - see the note where it
# is used.
HARD_REJECT_MARGIN = 0.10


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(int(len(ordered) * pct / 100), len(ordered) - 1)
    return ordered[index]


def _best_distance(retriever, question: dict, top_k: int) -> float | None:
    """Smallest cosine distance the matter offers for this question, which is
    exactly the quantity the gate in `_clears_relevance_floor` tests."""
    chunks = retriever.retrieve(
        question["question"],
        top_k=top_k,
        matter_id=question["matter_id"],
        jurisdiction=question["jurisdiction"],
    )
    distances = [c.vector_distance for c in chunks if c.vector_distance is not None]
    return min(distances) if distances else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    from policy_advisor.retrieval.hybrid_retriever import HybridRetriever

    retriever = HybridRetriever(max_distance=GATE_DISABLED)
    questions = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))

    positives: list[tuple[str, float]] = []
    negatives: list[tuple[str, float]] = []
    exact_match_only: list[str] = []

    for question in questions:
        best = _best_distance(retriever, question, args.top_k)
        if best is None:
            # Locator short-circuit hits have no vector distance. They bypass
            # the gate by design, so they can't constrain the threshold.
            exact_match_only.append(question["id"])
            continue
        (negatives if question["expect_refusal"] else positives).append((question["id"], best))

    print("positives (corpus should answer these):")
    for qid, distance in sorted(positives, key=lambda p: p[1]):
        print(f"  {qid:5s} {distance:.4f}")
    print("\nnegatives (corpus cannot answer these):")
    for qid, distance in sorted(negatives, key=lambda p: p[1]):
        print(f"  {qid:5s} {distance:.4f}")
    if exact_match_only:
        print(f"\nexact locator matches, exempt from the gate: {', '.join(exact_match_only)}")

    if not positives or not negatives:
        raise SystemExit("\nNeed both positives and negatives in the golden set to calibrate.")

    worst_positive = max(d for _, d in positives)
    best_negative = min(d for _, d in negatives)
    margin = best_negative - worst_positive

    print("\n--- separation ---")
    print(f"worst positive: {worst_positive:.4f}")
    print(f"best negative : {best_negative:.4f}")
    print(f"margin        : {margin:+.4f}")

    # A single threshold is only trustworthy if the two populations are far
    # apart. Separable-but-touching means the threshold has been fitted to the
    # handful of questions in this file and will misclassify the next one.
    if margin < MIN_TRUSTWORTHY_MARGIN:
        print(
            f"\nToo close to split on distance alone (margin < {MIN_TRUSTWORTHY_MARGIN}). Any single\n"
            "threshold here is fitted to this golden set, not to the corpus. Use the two-band\n"
            "configuration: accept cheaply when clearly relevant, reject cheaply when clearly\n"
            "irrelevant, and spend one Claude call adjudicating the middle."
        )
    else:
        print("\nWide enough to split on distance alone, but the two-band values below still apply.")

    # The two thresholds are not calibrated the same way, because being wrong
    # about them costs different things.
    #
    # CERTAIN can be fitted to the observed positives: setting it too high
    # auto-accepts a weak chunk, and the grounded prompt still refuses when the
    # passage doesn't answer the question.
    #
    # MAX cannot. Setting it too low hides a document the lawyer uploaded, with
    # no later stage able to recover it, and this golden set covers a single
    # matter of long formal civil-procedure text. Distances in a short contract
    # or a witness statement sit much higher for questions the document plainly
    # answers, so a MAX fitted to the negatives here will reject real documents
    # elsewhere. It therefore sits a clear margin beyond the worst thing this
    # corpus has ever scored, and only buys the cheap rejection of the
    # obviously unrelated.
    certain = _percentile([d for _, d in positives], 50)
    reject = max(d for _, d in negatives) + HARD_REJECT_MARGIN
    print("\n--- recommended configuration ---")
    print(f"RETRIEVAL_CERTAIN_DISTANCE={certain:.2f}   # at or below: relevant, no LLM call")
    print(f"RETRIEVAL_MAX_DISTANCE={reject:.2f}       # above: unrelated, no LLM call")
    print(
        "\nMAX sits a margin beyond the worst negative on purpose - it is a cost optimisation,\n"
        "not the classifier. Tightening it toward the negatives saves calls and starts hiding\n"
        "real documents in matters that look nothing like this corpus."
    )

    adjudicated = [q for q, d in positives + negatives if certain < d <= reject]
    auto_accept = [q for q, d in positives + negatives if d <= certain]
    auto_reject = [q for q, d in positives + negatives if d > reject]
    total = len(positives) + len(negatives)
    print(
        f"\nOn this golden set: {len(auto_accept)} auto-accepted, {len(auto_reject)} auto-rejected, "
        f"{len(adjudicated)} adjudicated ({100 * len(adjudicated) / total:.0f}% of queries pay one extra call)."
    )
    wrongly_rejected = [q for q, d in positives if d > reject]
    wrongly_accepted = [q for q, d in negatives if d <= certain]
    print(f"positives auto-rejected without review: {wrongly_rejected or 'none'}")
    print(f"negatives auto-accepted without review: {wrongly_accepted or 'none'}")
    print(
        "\nBoth lists must stay empty: those are the decisions no LLM ever gets to correct.\n"
        "Widen the band if either fills up."
    )


if __name__ == "__main__":
    main()
