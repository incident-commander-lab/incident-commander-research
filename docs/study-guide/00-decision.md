# 0. The decision (11 Oct 2026, updated 12 Oct: live system un-parked)

**One idea per route, finished in order. No new directions until these are done.**

| Route | Idea | Why this one | Status |
|---|---|---|---|
| **Paper 1** | **RQ1 + RQ2 + RQ3**: a fixed rule beats a plain LLM agent at ~1/10 of the tokens and ties an LLM choosing at ~1/3 (RQ1); act-or-escalate (RQ2); **the live system (RQ3)**, which also confirms rule v2 on faults it has never seen | RQ1/RQ2 done except the re-runs; RQ3 is the mentor's live system | Night 5 paused, Night 6 next; live system build starts now |
| **Paper 2** | **The Upgrade Paradox**: a smarter LLM can make an "LLM proposes, Bayes decides" system *worse* | Our own result (14B: 45% → 26%), with a diagnosed mechanism and a fix (rule v2). Nothing close found in ~12 searches. Publishable without touching the patent | **Next experiment** |
| **Patent** | **Autonomy Drift** (with re-tests inside it) | The only idea where the specific mechanism wasn't found in the prior-art screen | Confidential. One meeting with tech-transfer, nothing else until then |
| **Live system** | Wikipedia stream → Node.js collector → Redis → workers → Postgres → API; Prometheus + Alertmanager → agent → fix or ask in Slack | **Un-parked (12 Oct).** It is Paper 1's RQ3 (live operation + held-out test of rule v2, the reviewers' main objection), the mentor's request, the patent's future test bed and the startup's demo | **Minimal version first (2 weeks)**, then add Bluesky / GitHub / GH Archive replay |
| **Startup** | "Earning Autonomy": an AI SRE layer that decides when *not* to act, sold to teams that can't hand production to a black box (regulated/private-cloud companies, e.g. Egypt's banks and telcos under PDPL) | Papers = credibility, patent = protection, live system = demo. Starting it before those exist means pitching claims we can't back | **Later**: decide after Paper 1 is submitted and the tech-transfer meeting |

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
| 12–25 Oct | **Live system, minimal version** (see chapter 9): week 1 = one stream (Wikipedia) + collector + Redis + worker + Postgres + Prometheus metrics + Grafana; week 2 = 4 injected faults with a sealed ground-truth log + Alertmanager → agent (diagnosis first, then reversible actions behind OPA) + Slack escalation + recorder for replay | Nour leads the app, Shahesta the data pipeline + agent wiring, Claude builds alongside |
| when the laptop is free | Resume Night 5 (paused 12 Oct at 543 runs; ~4 h left) → `fetch`, audit, charts → Night 6 (~10–12 h) | Shahesta |
| 12–14 Oct | **Upgrade Paradox, Experiment 1 (model ladder):** qwen3 0.6B / 1.7B / 4B / 8B / 14B write hypotheses for the 73 problems; offline replay against the full "every check" readings; rule v1 vs v2 | Claude + Shahesta (laptop only, no AWS) |
| 14 Oct | **Go / no-go:** under rule v1, does accuracy fail to rise with model size, *and* does v2 fix it? | Both |
| 15–18 Oct | If go: Experiment 2 (the specificity dial) + a second model family. If no-go: write Paper 1 only | Both |
| this week | Email the university tech-transfer office about a confidential invention disclosure | Shahesta |
| 19–25 Oct | Night 6 results into Paper 1; draft Sections 1–3 (RQ1/RQ2 written, RQ3 section waits for live results) | Both |
| 26 Oct → | Live pilot sessions on AWS (4–6 h each), then the RQ3 experiment: rule v2 (frozen) vs plain LLM agent vs always-escalate on the same replayed traffic and faults | Both |

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
