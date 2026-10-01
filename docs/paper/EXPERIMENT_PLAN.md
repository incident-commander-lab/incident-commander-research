# Experiment Plan: LLM Proposes, Decision Theory Decides

## Research question

When an LLM agent diagnoses an incident, does handing **probe selection and stopping** to a Bayesian value-of-information (VoI) layer, while the LLM still **generates the hypotheses**, give the same or better accuracy with fewer investigation steps and fewer tokens?

## Hypotheses (stated before running)

- **H1 (efficiency):** VoI-driven probe selection uses fewer probes and fewer tokens than an LLM choosing its own next step, at equal or better root-cause accuracy.
- **H2 (stopping):** The VoI stopping rule removes the long failed trajectories (repeated queries, over-investigation) reported for pure LLM agents.
- **H3 (calibration):** Posterior beliefs are better calibrated (lower Brier score) than the LLM's self-reported confidence.

Negative results on any of these get reported as-is.

## Benchmark

**Primary: AIOpsLab** (Microsoft). It has a standard agent interface, fault injection on real microservice apps (Kubernetes), detection / localization / root-cause-analysis / mitigation tasks, and published LLM-agent baselines to compare against.
**Fallback / second: ITBench** (IBM), SRE scenarios.
Before committing, check each benchmark's current task count, license, and hardware needs.

Our own four chaos scenarios stay in the paper only as a mechanism demo, not as the evaluation.

## Conditions (the ablation)

All conditions use the same LLM, temperature, tool access and step budget.

| ID | Hypotheses from | Next probe chosen by | Stops when |
|---|---|---|---|
| A | LLM (implicit) | LLM, ReAct loop | LLM says done / budget |
| B | LLM, explicit list | LLM | LLM says done / budget |
| C | LLM, explicit list | **VoI (EIG ÷ cost)** | **no probe has EIG above threshold, or posterior ≥ τ** |
| D | LLM, explicit list | Random probe | same rule as C |
| E | LLM, explicit list | Exhaustive (all probes) | all probes run |

- **A vs C** is the headline result.
- **B vs C** isolates the decision layer (the hypothesis list is the same in both).
- **D** shows VoI ordering matters, not just having a stopping rule.
- **E** is the accuracy ceiling for C's hypothesis set.

## Metrics

| Metric | Why |
|---|---|
| Localization accuracy (top-1, top-3) | Correctness |
| Root-cause accuracy | Correctness |
| Probes per incident | Efficiency (H1) |
| LLM input + output tokens, and cost in $ | Efficiency (H1) |
| Wall-clock time to diagnosis | Operational value |
| Repeated-query rate; trajectory length on failures | Over-investigation (H2) |
| Brier score and reliability curve | Calibration (H3) |
| Hypothesis recall: was the true cause in the LLM's list? | Separates generation errors from decision errors |

Hypothesis recall matters: if C fails, it tells us whether the LLM never proposed the right cause, or whether the decision layer picked wrong.

Run each condition with at least 3 seeds and report confidence intervals. Use paired tests per problem (e.g. Wilcoxon on probes, McNemar on accuracy).

## What needs building

1. **Generic probe templates** (the main engineering task). Today `ic/live/catalog.py` has three fixed observables. The benchmark needs parameterised probes over its telemetry: `metric_trend(service, metric)`, `log_pattern(service, pattern)`, `trace_error_hop(service)`, `recent_change(service)`, each mapping its raw result to a small set of discrete outcome labels. Each probe has a cost (latency or tokens).
2. **Hypothesis generator adapter.** Extend `ic/dynamic_triage.py` so that for each hypothesis the LLM predicts an outcome label for each probe template it can see. Keep the anti-hallucination filter: drop predictions that name probes or services that don't exist.
3. **Decision loop.** Reuse `ic/live/beliefs.py` unchanged (Bayesian update and EIG) and add the stopping rule.
4. **Hypothesis expansion.** If every hypothesis is contradicted (posterior mass collapses), re-ask the LLM once with the evidence gathered so far. This is the honest answer to "the true cause wasn't in the list."
5. **Baselines A, B, D, E** in the same harness.
6. **Logging.** Every probe, token count and posterior goes into the existing evidence chain, so every trajectory is auditable and reproducible.

## Milestones

| # | Milestone | Done when |
|---|---|---|
| 1 | Benchmark running locally | One problem solved end to end by the benchmark's own example agent |
| 2 | Probe templates + adapter | Condition C runs on 5 problems |
| 3 | All 5 conditions | Pilot on ~10 problems, numbers sanity-checked |
| 4 | Full run | All problems × 5 conditions × 3 seeds |
| 5 | Analysis + write-up | Tables, failure taxonomy, paper draft |

## Risks

- **Discretising telemetry into outcome labels** may lose signal. Mitigation: report the label thresholds and do a sensitivity analysis.
- **The LLM's predictions may be poor** (wrong likelihoods). Mitigation: the ε measurement-error term already softens them; report hypothesis recall separately.
- **Cost.** 5 conditions × N problems × 3 seeds of LLM calls. Mitigation: pilot on a pinned cheap model, run the final pass on one strong model.
- **Hardware.** AIOpsLab needs a Kubernetes cluster (kind locally, or a cloud VM).

## Framing for the paper

Thesis: reversibility, not confidence. Diagnosis decisiveness comes from a principled, auditable decision layer rather than the LLM's own judgement. This experiment supplies the evidence that the layer is also more efficient, not only safer.
