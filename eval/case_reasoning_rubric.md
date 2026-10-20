# Case-reasoning evaluation rubric (human-reviewed)

CLAUDE-2.md capability 2: recall/precision/MRR (`eval/run_golden_set.py`) evaluate
*retrieval*. They don't evaluate whether a reasoned legal argument is sound -
that needs someone with legal judgment, scoring against this rubric. Run this
whenever the case-reasoning prompts, retrieval logic, or model changes -
treat it as a regression check, not a one-time sign-off.

## Process
1. Run `eval/case_reasoning_seed_cases.json` (or a case of the reviewer's own choosing)
   through `CaseReasoningChain.analyze()`.
2. A reviewer with legal judgment scores the output against the five criteria
   below, on a 1-3 scale (1 = fails, 2 = partially meets, 3 = meets).
3. Record the score, the reviewer, the date, and the model/prompt version in
   a log (not specified further here - wire this to whatever tracking the
   team already uses once this graduates past a single reviewer).
4. There is no automated pass/fail threshold for this rubric, deliberately -
   unlike retrieval recall@k, "is this argument sound" doesn't reduce to a
   number a script can gate on. The threshold is a human's judgment call,
   informed by trend data across runs (is this case-reasoning output getting
   better or worse over time, not just "did it clear an arbitrary bar today").

## Scoring criteria

### 1. Issue identification
Did the identified issues match the legal questions a competent lawyer would
actually identify from the facts? Flag both misses (a real issue not raised)
and noise (an issue that doesn't actually arise from these facts).

### 2. Strongest-authority selection
For each issue, did the analysis cite the authorities a lawyer would
consider most directly on point - not just *any* authority that happened to
be retrieved? A technically-faithful citation to a weak or tangential
authority, while ignoring a stronger one that was also retrieved, is a real
quality failure even though `check_faithfulness` would pass it.

### 3. Confidence calibration
Does the stated confidence ("strongly supported" / "plausible, limited
authority" / "no authority found in corpus" / "unverified") match the actual
strength of what was retrieved? The single biggest risk in this feature
(per CLAUDE-2.md) is sounding equally confident regardless of authority
strength - score this strictly.

### 4. Citation faithfulness (spot-check beyond the automated layers)
The deterministic check and Claude-as-judge pass already ran - this is a
human spot-check for what both might miss: a citation that exists and isn't
flagged, but is still a stretch of what the cited passage actually says.

### 5. Framing and disclaimer
Does the output ever drift into language that sounds like a prediction of
an actual court's decision (forbidden, per CLAUDE-2.md - a hard requirement,
not a style preference)? Is the disclaimer present and not buried?

## Notes for the reviewer
- An issue marked `unverified` by the automated checks is *expected* to look
  weaker - that's the system working, not a bug. Don't penalize the output
  for flagging its own uncertainty; penalize it for *not* flagging
  uncertainty it should have.
- This pipeline runs many checked reasoning steps per case (see
  `generation/case_reasoning.py`) and can take several minutes per case -
  budget review sessions accordingly rather than batching dozens of cases at
  once early on.
