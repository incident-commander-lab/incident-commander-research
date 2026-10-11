# 0. The decision (11 Oct 2026)

**One idea per route, finished in order. No new directions until these are done.**

| Route | Idea | Why this one | Status |
|---|---|---|---|
| **Paper 1** | **RQ1 + RQ2**: a fixed rule beats a plain LLM agent at ~1/10 of the tokens, and ties an LLM choosing at ~1/3 | Done except the clean re-runs | Night 5 running, Night 6 next |
| **Paper 2** | **The Upgrade Paradox**: a smarter LLM can make an "LLM proposes, Bayes decides" system *worse* | Our own result (14B: 45% → 26%), with a diagnosed mechanism and a fix (rule v2). Nothing close found in ~12 searches. Publishable without touching the patent | **Next experiment** |
| **Patent** | **Autonomy Drift** (with re-tests inside it) | The only idea where the specific mechanism wasn't found in the prior-art screen | Confidential. One meeting with tech-transfer, nothing else until then |
| **Demo** | The RQ3 live system | Needed by the patent idea and for a strong demo later | **Parked** until Paper 2's result is in |

## Dropped or parked (and why)

| Idea | Decision | Reason |
|---|---|---|
| Evidence independence / double counting | **Dropped** as a direction | 3 papers in Sept–Oct 2026 + "data incest" literature. Its useful part (our checks are correlated) goes into Paper 2 as part of the mechanism |
| Agents conflicting with each other | Dropped | Solved in telecom (O-RAN xApp conflicts) |
| Proof-carrying actions | Dropped | Several 2026 papers |
| Planted exams / adaptive testing as its own idea | Folded into the patent | Its parts exist (SAIBERSOC, ATLAS, US 12602527); only the combination with Autonomy Drift may be open |
| New decks, new pitches | Parked | No new public material until Paper 2's result and the patent decision |

## The next 14 days

| When | What | Who |
|---|---|---|
| 12 Oct (morning) | Night 5 done → `fetch`, audit, charts → start Night 6 | Shahesta |
| 12–14 Oct | **Upgrade Paradox, Experiment 1 (model ladder):** qwen3 0.6B / 1.7B / 4B / 8B / 14B write hypotheses for the 73 problems; offline replay against the full "every check" readings; rule v1 vs v2 | Claude + Shahesta (laptop only, no AWS) |
| 14 Oct | **Go / no-go:** under rule v1, does accuracy fail to rise with model size, *and* does v2 fix it? | Both |
| 15–18 Oct | If go: Experiment 2 (the specificity dial) + a second model family. If no-go: write Paper 1 only | Both |
| this week | Email the university tech-transfer office about a confidential invention disclosure | Shahesta |
| 19–25 Oct | Night 6 results into Paper 1; first draft of Paper 1 | Nour leads the draft, Shahesta the results |

## Go / no-go rule for the Upgrade Paradox (set *before* seeing results)

- **Go** if, under rule v1, at least one larger model scores lower than a smaller one by more than the noise (a paired test, p < 0.05), **and** under rule v2 the drop disappears.
- **No-go** if accuracy rises steadily with size under v1. Then the 14B result was a one-off, and it becomes one paragraph in Paper 1.

Either way, we report what we find.

## Rules until 25 Oct

1. No new idea gets work time. Write it in the parking lot below and move on.
2. Nothing about Autonomy Drift in any public place.
3. Every number shown anywhere comes from the audited, re-run data.

## Parking lot

- (add new ideas here, with the date)
