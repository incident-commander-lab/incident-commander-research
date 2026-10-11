# Incident Commander: Study Guide

A guide for Shahesta and Nour to everything built and run so far, and what comes next. Each chapter is short and has pictures. Read them in order the first time.

> **Status (11 Oct 2026):** RQ1 benchmark: Nights 1–4 are done. An audit found a measurement bug (two "broken" checks), so **Nights 5–6 are re-running the 201 affected runs**. The numbers in chapter 4 are **provisional** until those nights finish, and the charts are regenerated afterwards.

| # | Chapter | What you'll learn |
|---|---|---|
| 0 | [**The decision (read first)**](00-decision.md) | What we're doing, what we dropped, the next 14 days |
| 1 | [The big picture](01-big-picture.md) | The problem, our idea in one sentence, the glossary |
| 2 | [Phase 1: the demo system](02-phase1-demo.md) | What Incident Commander does: console, OPA, verification, evidence chain |
| 3 | [The decision rule](03-decision-rule.md) | How the rule chooses checks and when it stops, with a worked example; rule v1 vs v2 |
| 4 | [RQ1: the benchmark](04-rq1-benchmark.md) | AIOpsLab, the six setups, the AWS + laptop setup, the results |
| 5 | [Run diary: every problem and fix](05-run-diary.md) | What went wrong during the nights, whether it changed results, and the audit |
| 6 | [RQ2: act or escalate (simulator)](06-rq2-simulator.md) | When the rule fixes, waits or calls a human |
| 7 | [Related work and competitors](07-related-work.md) | Who else is close, and what's actually new about ours |
| 8 | [What we published](08-published.md) | Decks, Research Radar, portfolio, Zenodo, GitHub, with screenshots |
| 9 | [RQ3: the live system (plan)](09-rq3-live-plan.md) | The new part: a real app fed by real public data streams |
| 10 | [How to run things](10-how-to-run.md) | Runbook commands, watching a night, fetching results, making charts |

**Confidential:** the Autonomy Drift idea (the possible patent) is **not** in this repo. It lives in `private/` on Shahesta's laptop. Don't add it here, to decks, talks or the paper until we've decided about filing (Egypt has no grace period: publishing first kills the patent).

### Regenerating the pictures

```bash
python3 docs/study-guide/tools/make_charts.py results/aiopslab_full_rq1/results   # charts from results
python3 docs/study-guide/tools/audit_runs.py  results/aiopslab_full_rq1/results   # error audit
```

Run both after `~/ic-bench.sh fetch`.
