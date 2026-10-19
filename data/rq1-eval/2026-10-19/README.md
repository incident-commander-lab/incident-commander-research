# RQ1 offline evaluation on the final results (19 Oct 2026, Nour)

Calibration / selective prediction and the evidence-failure test (`ic/eval`, PR #3) rerun on the
**final** RQ1 snapshot. These replace the 12 Oct numbers for every use in the paper.

## Provenance

| | |
|---|---|
| Input | `data/rq1-results/2026-10-15/full_rq1.jsonl.gz`, 730 runs, SHA-256 `a4cf24db…71aa2` (verified against `SHA256SUMS`) |
| Code | `ic/eval` at commit `09c16cd` (unchanged since PR #3), Python 3.14 |
| Sanity | all 10 overall accuracies equal the final snapshot's table; evidence-failure at mask 0% reproduces 100% of saved answers |
| Per task | the same tools run on the input split by `task`: detection (32 problems, 320 runs) and localization + analysis (41 problems, 410 runs) |

```bash
python -m ic.eval.calibration      data/rq1-results/2026-10-15/full_rq1.jsonl.gz --out data/rq1-eval/2026-10-19/calibration
python -m ic.eval.evidence_failure data/rq1-results/2026-10-15/full_rq1.jsonl.gz --out data/rq1-eval/2026-10-19/evidence_failure
# per task: same commands on the input filtered to task == detection, and to task in {localization, analysis}
```

| Path | Content |
|---|---|
| `calibration/`, `calibration.txt` | all tasks: Brier, ECE, AURC, E-AURC, selective accuracy, risk-coverage plot |
| `evidence_failure/`, `evidence_failure.txt` | all tasks: accuracy, confidence, Brier at 0/10/20/30/50% of paid checks failed |
| `by_task/<detection|loc_analysis>/…` | the same two analyses per task |

## Findings

**1. The evidence-failure "robustness" is a detection artifact; report it per task.**
Over all tasks, accuracy *rises* as checks fail (8B rule v1 42% → 48% at 50% masked; 14B rule v1
26% → 32%). Split by task, the whole rise is on detection (8B rule v1 69% → 85%, rule v2 81% → 93%),
where answering "Yes" is right on 29 of 32 problems: fewer healthy readings leave the posterior on
"some fault". On localization + analysis, accuracy is flat or slightly down for every method
(8B rule v1 22% → 20%, rule v2 22% → 24%, exhaustive 17% → 18%). The overall table must not be
reported as "robust to failing checks".

**2. Rule v1 becomes overconfident as evidence fails.** On localization + analysis, 8B rule v1's
mean confidence rises 43% → 51% while accuracy stays at 20–22%; Brier 0.183 → 0.283.

**3. Rule v2 is well calibrated on detection and badly overconfident on localization + analysis.**
Detection: ECE 0.05 (8B and 14B), and at τ = 0.78 it keeps 41–62% of problems at 95–100% accuracy.
Localization + analysis: mean confidence 64–71% against 22–29% accuracy, ECE 0.35–0.49; at τ = 0.78
it keeps 49–59% of problems and gets only 38–40% of those right. Every posterior method except
rule v1 is overconfident there (LLM-chooser ECE 0.35–0.38, exhaustive 0.43). The v2 catch-all
hypotheses fix the detection collapse but leave the localization posterior concentrated on a
service it often gets wrong.

**4. Changes from the 12 Oct run.** 8B results are identical (those nights were already complete).
14B was incomplete on 12 Oct (rule v1 50 runs, rule v2 42, no LLM-chooser); final: 73 each.
14B rule v1 34% → 26%, rule v2 60% → 52%; the 14B LLM-chooser (55%) is new.

## Implications

- **Paper 1:** report calibration and evidence failure per task, with the always-"Yes" baseline next
  to every detection number. Finding 3 is a limitation to state: the posterior's confidence is
  only trustworthy for detection.
- **RQ3:** the test-bed agent acts on P(fix works), not on a localization posterior, but it is
  built from the same kind of beliefs. Per-setup calibration (already in the protocol) is the
  check that matters; finding 3 says to expect overconfidence and to read the reliability table
  before trusting τ = 0.78.
- **Rule v3 candidate (held-out only):** temper the localization posterior (e.g. raise the catch-all
  prior or calibrate on held-out data). Not to be tuned on these 73 problems.
