# 6. RQ2: act or escalate (simulator)

Code: `ic/rq2/sim.py` (incidents), `ic/rq2/policies.py` (who decides), `ic/rq2/run.py`. Results: `docs/paper/rq2-findings/`.

## The question

Diagnosis is half the job. Then you must choose: **fix it now** (reversible actions only), **look more**, or **call a human**. Acting wrongly causes harm; calling a human costs about 10 minutes and a page; waiting costs customer impact every minute.

## The simulator

Each simulated incident has a hidden true cause: a bad deploy, a dependency, the database, or something unknown.

| Action | Effect |
|---|---|
| Rollback, inventory fallback | reversible: fixes the right cause, harmless otherwise (with a fixed cost) |
| Database failover | irreversible: **always denied** by policy |
| Escalate | a human takes about 600 s, plus a fixed paging cost |
| Check | costs a little time, gives a noisy reading |

Customer impact `r` scales how expensive every minute of waiting is: low (0.25), moderate (1), high (4), critical (16).

## The policies compared

| Policy | How it decides |
|---|---|
| **ours** | full expected-cost plan over beliefs: checks, actions and escalation in one utility |
| ours_myopic | same, one step ahead only |
| gira | GIRA-style (Patel, ICLR'26 workshop): impact reduction − risk − human cost, **no information term** |
| conf70 | act if LLM-style confidence ≥ 0.7, else escalate (Wang et al. 2025) |
| diag_first | always diagnose fully, then act |

## Results (main sweep)

Cost per incident (lower is better):

| Impact | ours | ours_myopic | gira | conf70 | diag_first |
|---|---|---|---|---|---|
| low (0.25) | **292** | 305 | 375 | 324 | 322 |
| moderate (1) | **583** | 595 | 834 | 632 | 673 |
| high (4) | 1894 | 1891 | 2290 | **1866** | 2076 |
| critical (16) | **6591** | 6730 | 7660 | 6799 | 7688 |

- **Ours is cheapest at 3 of 4 impact levels.** At high impact, conf70 is 2% cheaper. Cause: the agent assumed 10% unknown causes while the simulator had 25%. With matched assumptions, ours is about 15% cheaper.
- **Sensible behaviour:** as impact rises, ours acts sooner (the bar to act falls from about 95% to 75% belief), because waiting gets expensive.
- **gira** (no information term) acts blindly at moderate and higher impact: 75% harmful actions.

## Sensitivity (impact high)

1. **Wrong guess about unknown causes:** ours stays cheaper than conf70 and diag_first in most settings. It loses only when the world has far more unknown causes (40%) than the agent assumes (5–10%), so **don't under-estimate the unknown share**.
2. **Noisier checks than assumed:** ours' advantage *grows* with noise against conf70 and diag_first, and shrinks against gira at triple noise.

## Limits

It's a simulator: we wrote the world, so it shows the logic is sound, not that it works in production. RQ3 (chapter 9) is the real-world confirmation, with real actions and a real human in Slack.
