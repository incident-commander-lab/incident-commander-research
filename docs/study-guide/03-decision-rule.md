# 3. The decision rule

Code: `ic/bench/decide.py` (the rule), `ic/bench/agent.py` (the agent around it).

## In plain words

1. The LLM reads the alert and the pod list and writes **hypotheses**: possible causes, each with what it *predicts* every check would show. ("If geo crashed, geo's pods are unhealthy and its logs show crashes.")
2. We add two hypotheses the LLM didn't write: **"no fault"** (everything looks healthy) and, in rule v2, one **"something else is wrong with service X"** per service.
3. Each hypothesis gets a probability (its **belief**).
4. For every check we could run, the rule computes **how much it would probably teach us** (EIG, in "nats") minus **what it costs**:

   ```
   VoI(check) = EIG(check) − λ · cost(check)          λ = 0.02
   ```

5. Run the check with the highest VoI, read the result, and update all beliefs with Bayes' rule.
6. **Stop when no check has VoI > 0.** Nothing left is worth its price.
7. Answer from the beliefs.

The LLM never decides what to check or when to stop. In a run it is called only about 1–3 times (to write hypotheses), against about 17–22 calls for a plain LLM agent.

## How a reading changes beliefs

Each check has a list of possible results, e.g. `pod_health` → `unhealthy | healthy`. For each hypothesis:

| Situation | Likelihood of the reading |
|---|---|
| The hypothesis predicted exactly this | high: `1 − ε` |
| It predicted something else | low: `ε / (k − 1)` |
| It predicted a symptom, but the reading is healthy | `miss` (symptoms can be silent, e.g. logs miss 40% of faults) |
| It said nothing about this check | neutral: `1 / k` |

`ε` is how often a check lies: 0.05 for kubectl state, 0.10 for events, 0.25 for a regex over logs, 0.15 for an LLM log reading. Beliefs are multiplied by these likelihoods and re-normalised.

## Worked example (real output of the code)

Two services, `geo` and `frontend`. Truth: **frontend points at the wrong port**.

| Hypothesis | Start belief |
|---|---|
| H1 geo crashed | 0.34 |
| H2 frontend wrong port | 0.31 |
| U1 / U2 something else in geo / frontend | 0.05 each |
| H0 no fault | 0.25 |

The rule scores all 12 checks. The top two are `pod_health:geo` (VoI 0.327) and `service_ports:frontend` (0.320); log checks cost twice as much and score only about 0.04.

| Step | Check (VoI) | Reading | Beliefs afterwards | P(fault) |
|---|---|---|---|---|
| 1 | pod_health:geo (0.33) | healthy | H1 0.07 · H2 0.45 · H0 0.36 | 0.64 |
| 2 | service_ports:frontend (0.34) | **port_mismatch** | **H2 0.87** · H0 0.04 | 0.96 |
| 3–7 | events, pods, deployment, endpoints | all healthy | H2 drifts down to 0.34, H0 up to 0.59 | 0.41 |
| 8 | log_signature:frontend (0.04) | connection_errors | **H2 0.86** | 0.89 |
| | stop: no check worth its cost | | answer: **frontend** (0.88) | |

**What to notice:**
- The first two checks are chosen because they *split* the hypotheses: each one is predicted differently by different causes.
- Expensive log reads come last.
- **A weakness, steps 3–7:** H2 said nothing about frontend's pods or endpoints, so it is neutral (1/6) on them, while "no fault" predicted healthy (0.9). Each healthy reading on the faulty service nudges belief back toward "no fault". Faults that leave most readings healthy (a wrong port, a flapping pod) can be talked out of. This is the same mechanism behind the 14B collapse (chapter 5) and the false alarms on no-fault problems. **We are noting it, not changing the rule now:** changing the rule mid-experiment would be fitting to the test set again. It is a candidate for rule v3, to be tested on held-out data.

## Rule v1 vs rule v2

Rule v2 was made on 10 Oct after the 14B model collapsed to 26% with v1 (chapter 5).

| | Rule v1 | Rule v2 |
|---|---|---|
| Hypotheses | LLM's list + "no fault" | + one **"something else in X"** catch-all per service (prior 0.10 in total) |
| "No fault" hypothesis | treated like an LLM guess (extra error margin), so abnormal readings hit it only weakly | treated as a definition: an abnormal reading counts fully against it |
| Detection answer ("is something broken?") | yes if the top hypothesis is a fault | yes if **P(fault) > 0.5** (sums every fault hypothesis) |
| Localization answer | service of the top hypothesis | service with the most total belief |
| Switch | `IC_RULE_VERSION=1`, condition `voi` | condition `voi2` |

**Honest caveat:** v2 was designed *after* seeing these 73 problems. The paper must report v1 and v2, and confirm v2 on problems it has never seen (RQ3 does this, chapter 9).

## The replay tool

`python -m ic.bench.replay results/.../full_rq1.jsonl` re-decides saved runs offline with another rule version, with no cluster and no LLM. It reproduced all 146 saved v1 answers exactly, and predicted the live v2 outcome on 65 of 73 problems. It's how we test rule ideas cheaply, as long as we remember that tuning on the same problems is not evidence.
