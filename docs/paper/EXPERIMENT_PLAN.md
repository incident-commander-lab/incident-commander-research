# Experiment Plan

**Working title:** From Guessing to Experiment Selection: Value-of-Information-Guided LLM Agents for Incident Diagnosis

## Central question

Can separating **hypothesis generation** (an LLM proposes candidate root causes) from **experiment selection** (a value-of-information policy decides what to observe next, and when to stop) reduce wasted investigation while keeping or improving diagnostic accuracy?

The model is the experimental **instrument**. The variable under study is the **decision architecture**: who chooses the next observation, and who decides to stop.

Novelty wording: we investigate explicit VoI-based experiment selection for LLM-driven incident diagnosis. We do **not** claim to be the first to apply VoI to diagnosis (cf. Rish 2005 on probing; guarded tool-using agents for incident response; LLM-assisted Bayesian experiment design in science). The related-work section establishes how close prior combinations come.

## Research questions

| | Question | Main comparison | Status |
|---|---|---|---|
| **RQ1 (core)** | Does VoI-based experiment selection improve investigation efficiency without reducing diagnostic accuracy? | Plain LLM agent vs LLM + VoI (with ablations) | Harness built and piloted |
| **RQ2** | When does a *reversible* remediation have more combined mitigation-plus-information value than further observation? | Diagnose-first vs act-to-learn, sweeping customer impact | Planned |
| **RQ3** | How robust is decision-theoretic investigation to corrupted telemetry? | Plain LLM vs LLM + VoI, clean vs poisoned observations | Planned (robustness section) |

## Hypotheses (stated before the main runs)

- **H1 (efficiency):** VoI selection uses fewer paid probes and fewer *decision* LLM calls than an LLM choosing its own next step, at equal or better accuracy.
- **H1a (stopping):** The VoI stopping rule removes long, repetitive failed trajectories without raising the premature-stop rate.
- **H1b (calibration):** The posterior is better calibrated (lower Brier score) than the LLM's self-directed stopping.
- **H2 (act-to-learn):** As customer-impact cost rises, a policy that values mitigation and information jointly reduces time-to-mitigation without increasing harmful actions.
- **H3 (robustness):** Decision-theoretic selection reduces repeated investigation under misleading telemetry, but stays vulnerable to systematically corrupted observations (an expected boundary condition, reported either way).

Negative or mixed results are reported as they are.

## The decision rule

The same formula chooses and stops. For every unrun probe *a*:

    VoI(a) = EIG(a) − λ · cost(a) − μ · risk(a)

- **choose** argmax_a VoI(a)
- **stop** when max_a VoI(a) ≤ 0, meaning no probe is worth what it costs

`EIG` is the expected reduction in posterior entropy (nats) under a noisy-observation likelihood. `cost` is the probe's relative cost. `risk` is 0 for read-only probes and is used by interventions in RQ2. **λ** (nats per unit of cost; default 0.05) is the single knob, and is swept in a sensitivity analysis. A probe budget is a safety cap only. Code: `ic/bench/decide.py`.

Belief model:
- Each hypothesis predicts an outcome label per probe. Silence about a probe on *another* service implies that service's healthy outcome (single-root-cause semantics).
- Likelihood: `1−ε` for a match, `ε/(k−1)` otherwise.
- **ε is per probe type:** 0.05 for kubectl state, 0.10 for events, 0.25 for regex over logs, 0.15 for a quote-checked LLM log reading.
- Discovery output (the pod listing) enters as free evidence.

## Conditions (RQ1 ablation)

All conditions use the **same model, tools, incidents, initial evidence and step budget**. The prompts necessarily differ, because the architectures ask the LLM different things; the paper states this explicitly.

| ID | Hypotheses | Next probe chosen by | Stops when | Isolates |
|---|---|---|---|---|
| A | none (implicit) | LLM, ReAct loop, full API | LLM submits | today's standard agent |
| B | LLM, explicit list | LLM | LLM says stop | B vs C: the decision layer alone (same hypotheses, beliefs tracked) |
| **C** | LLM, explicit list | **VoI** | **max VoI ≤ 0** | ours |
| D | LLM, explicit list | random among VoI > 0 | max VoI ≤ 0 | value of VoI *ordering* vs just a stopping rule |
| E | LLM, explicit list | every probe | all probes run | accuracy ceiling for the hypothesis set |

Further ablations: log reader regex vs LLM (`IC_LOG_READER`), λ sweep, discovery evidence on or off.

## Metrics (`python -m ic.bench.analyze`)

| Metric | Purpose |
|---|---|
| Accuracy with 95% Wilson CI (localization, detection, RCA; AIOpsLab's own grader) | primary outcome |
| Paid probes per incident; **decision** LLM calls; tokens in and out; wall time | efficiency (H1) |
| **Premature-stop rate**: wrong answers among runs that stopped by their own rule | "fewer probes because it gave up early?" |
| **Repeated actions**: share of actions identical to an earlier one | redundant investigation (H1a) |
| **Brier score** of final confidence vs correctness | calibration (H1b) |
| Hypothesis recall: was the true cause in the LLM's list? | separates generation errors from decision errors |
| Budget hits and LLM failures | reported separately, never silently dropped |

**Token claim, stated carefully.** The pilot showed that on an easy incident a plain agent can be cheaper: it looks once and answers. The claim to test is **bounded cost**: the plain agent re-sends its growing history at every step, while ours makes a fixed handful of calls. The paper plots tokens against investigation length and leads with B vs C, where C spends zero LLM calls on decisions.

## Benchmark

- **Primary: AIOpsLab** (Microsoft, MIT). 89 problems: 34 detection, 28 localization, 13 analysis, 14 mitigation. Our agents run the 75 non-mitigation problems across Hotel Reservation and Social Network, with Astronomy Shop if resources allow.
- **Secondary: shop-svc** (this repository), for RQ2 and RQ3, where reversible actions, customer impact and poisoned telemetry are controlled.

## Models

The model is frozen per experiment, and runs are strict single-model (no fallback; every row records the model that answered).

| Role | Model | Why |
|---|---|---|
| Reproducibility anchor | **Qwen3 8B, local via Ollama** | free, unlimited, open weights, runs on a 16 GB laptop |
| Open models, other labs | Laguna S 2.1, Nemotron 3 Ultra, Gemma 4 | the result should not depend on one lab |
| Closed reference | Gemini 3 Flash | "also holds on a frontier closed model" |

Log-reading quality varies by model. On the misconfiguration log, Laguna and Gemini 3 Flash spotted the bad port and Gemma 4 26B did not. It is reported per model.

## Repetitions and statistics

- At least 3 seeds per (model, condition) on the full set; 5 if budget allows.
- Paired per-problem tests: McNemar on accuracy, Wilcoxon on probes and tokens.
- Confidence intervals throughout.

**Budget sketch.** About 3.5 minutes per run on the AWS host.
- Local model: LLM cost $0.
- 75 problems × 5 conditions × 3 seeds ≈ 1,100 runs ≈ 65 h ≈ $18 of AWS credits on r7i.xlarge.

## Infrastructure

- `infra/bench-host.yaml`: one EC2 host, with no inbound ports, an idle auto-stop and a gross-cost budget alert.
- The stack is deleted between work sessions; results are copied locally first.
- Local model reached from the host over a tunnel (Tailscale or an SSH reverse tunnel over SSM).

## Status and pilot lessons (2026-10-02)

- The pipeline works end to end. First localization problem solved by Laguna and by Gemma 4 26B.
- Nine pipeline bugs were found and fixed, each with a regression test:
  - discovery evidence ignored
  - one fuzzy log reading swung the posterior
  - substring service matching
  - a reasoning setting that turned thinking on
  - prose around JSON
  - trailing JSON
  - rate-limit crashes
  - a preamble parsed as a service
  - result-label overwrite
- Pilot paused at 4 runs: the free cloud tiers (20 to 50 requests per day) are too small. This is why the local model is the anchor.
- Token cuts: the log reader runs only where the regex is blind; no re-generation over knock-on symptoms; a compact schema hint. The pilot replay now uses 2 LLM calls instead of 4.

## Threats to validity

- **Model dependence:** several models from several labs.
- **Discretising telemetry into labels:** thresholds are in code, with a sensitivity analysis.
- **Our own probe catalog:** the plain agent has the full API, so it is not handicapped.
- **Benchmark realism:** AIOpsLab faults are injected.
- **Single-root-cause assumption:** cascades are handled through implied healthy defaults and re-generation.
- **Free-tier nondeterminism:** strict single-model runs, with the answering model recorded.
