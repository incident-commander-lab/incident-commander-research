# Experiment Plan

**Working title:** Decision-Theoretic Troubleshooting, Revived: LLM-Built Hypothesis Models with Expected-Utility Decisions for Cloud Incidents

## Central question

Classical decision-theoretic troubleshooting chose the next observation, repair or configuration experiment by expected cost (Heckerman, Breese and Rommelse 1995; Breese and Heckerman 1996), and picked the most informative probe in distributed systems (Rish et al. 2005). It needed a Bayesian model hand-built for each system, which never kept pace with cloud systems that change weekly. LLM agents removed the model-building step but dropped the decision theory, and are documented to over-investigate, choose invalid recovery actions (R2Act: 36% valid) and abstain poorly (AgentAbstain: 59.5% at best).

**Question:** if an LLM builds the hypothesis model at runtime (candidate root causes, plus what each predicts every probe will show) and a fixed expected-utility rule outside the LLM decides whether to observe, intervene reversibly, escalate or stop, does incident response become more efficient and safer than letting the LLM decide?

The model is the instrument. The variable under study is the decision architecture: who chooses the next step, and who decides to stop or act.

**Novelty wording.** The decision theory is not ours, and the paper credits it. What we claim is the combination: LLM-instantiated hypothesis models feeding the classical decision rule, applied to cloud incidents, with reversibility and customer-impact cost in the utility, evaluated against LLM-directed agents. Closest recent neighbours, all cited: GIRA (expected-utility action gate with no information term), CIPHER-A (LLM-predicted outcomes gate actions, security), Wang et al. 2025 (escalation on LLM confidence < 0.7), Model Discovery Agent (LLM proposes plus VoI, science), Belief-State Engine (external belief, but the LLM still chooses actions). Full screen of 41 works: https://claude.ai/artifact/UhSAj4s9Ccx93vCbfV8Yuo

## Research questions

| | Question | Where | Status |
|---|---|---|---|
| **RQ1 (core)** | Does the decision rule beat LLM self-direction at choosing what to observe and when to stop, without losing accuracy? | AIOpsLab, 75 non-mitigation problems | Harness built and piloted |
| **RQ2** | Does the same rule decide well among acting, holding back and escalating, and does its behaviour shift sensibly with customer impact? | shop-svc: paired incidents (deploy causal / deploy not causal / ambiguous) at several customer-impact levels | To build |
| **RQ3** | On a live system with unannounced faults the rule has not been tuned on, does the frozen rule act safely and escalate when it should? | `testbed/` (live Wikimedia traffic); protocol in [`RQ3_PROTOCOL.md`](RQ3_PROTOCOL.md) | Test bed built; protocol in draft |

## Hypotheses (stated before the main runs)

- **H1 (efficiency):** VoI selection uses fewer paid probes and fewer *decision* LLM calls than an LLM choosing its own next step, at equal or better accuracy.
- **H1a (stopping):** The VoI stopping rule removes long, repetitive failed trajectories without raising the premature-stop rate.
- **H1b (calibration):** The posterior is better calibrated (lower Brier score) than the LLM's self-directed stopping.
- **H2 (act, hold back, escalate):** The expected-utility rule reaches higher paired action accuracy than an LLM actor, a GIRA-style utility without an information term, and a confidence-threshold escalator. As customer impact rises it shifts from observing to reversible intervention earlier, without more harmful actions.
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

## Conditions (RQ2, paired incidents in shop-svc)

Each incident comes in a pair (or triple) that differs by one controlled change, after AgentAbstain's design, so an always-act or always-hold policy cannot exceed 50%:

| Variant | Evidence | Correct decision |
|---|---|---|
| Deploy causal | error rate up, recent deploy, dependencies healthy, rollback restores health | roll back (reversible) |
| Deploy not causal | same alert and recent deploy, but the fault is elsewhere | do not roll back; keep observing or fix the real cause |
| Ambiguous | contradictory evidence, no safe action | escalate |

Each pair runs at several customer-impact levels (Incident Commander's CIS).

| ID | Who decides | Escalates when |
|---|---|---|
| A2 | LLM actor (ReAct with action tools) | the LLM decides to |
| G | GIRA-style utility: impact reduction − λ·risk − μ·human cost, **no information term** | no action has positive utility |
| W | LLM proposes; act if LLM confidence ≥ 0.7 (Wang et al. 2025) | confidence < 0.7 |
| **C2** | **ours: EIG + mitigation − λ·cost − μ·risk over the belief** | max utility of every autonomous action ≤ utility of escalation |

Metrics: paired action accuracy, harmful-action rate, correct-escalation rate, time to mitigation, customer impact accumulated (CIS × time), probes before acting. The OPA policy layer runs under every condition; it enforces permissions and is not the variable.

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

Frozen per experiment, strict single-model runs (no fallback; every result row records the model that answered).

| Role | Model | Why |
|---|---|---|
| Main, reproducible | **Qwen3 8B, local via Ollama** (reasoning off) | free, unlimited, open weights, runs on a 16 GB laptop; 3/3 valid hypothesis sets on the real 19-service prompt |
| Open models, other labs | Laguna S 2.1, Nemotron 3 Ultra, Gemma 4 | the result should not depend on one lab |
| Closed reference | Gemini 3 Flash | "also holds on a frontier closed model" (free tier is 20 requests/day, so small runs only) |

Log-reading quality varies by model: Laguna and Gemini 3 Flash spot the wrong-port clue in misconfig_app; Gemma 4 26B and Qwen3 8B do not. Reported per model; the regex reader is the fallback.

## Repetitions and statistics

- At least 3 seeds per (model, condition) on the full set; 5 if budget allows.
- Paired per-problem tests: McNemar on accuracy, Wilcoxon on probes and tokens.
- Confidence intervals throughout.

**Budget sketch.** About 3.5 minutes per run on the AWS host.
- Local model: LLM cost $0.
- 75 problems × 5 conditions × 3 seeds ≈ 1,100 runs ≈ 65 h ≈ $18 of AWS credits on r7i.xlarge.

## Infrastructure

- `infra/bench-host.yaml`: one EC2 host (r7i.xlarge, 4 vCPU / 32 GiB, fits a 5-vCPU quota). No inbound ports; idle auto-stop; gross-cost budget alert. Deleted between work sessions; results are copied locally first.
- **Local model over a reverse SSH tunnel through SSM.** The laptop runs Ollama on 127.0.0.1:11434. `ssh -R 11434:127.0.0.1:11434` over an `AWS-StartSSHSession` proxy makes it appear at 127.0.0.1:11434 on the host only (no port opens to the internet on either side). The agent on the host uses `IC_PROVIDER=openrouter_models IC_OPENROUTER_MODELS=ollama:qwen3:8b`. No API keys are needed on the host for local-model runs.

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
