# Cluster agent shakedown, 20 Oct 2026 (laptop, k3d; seen fault `assistant_bad_url` only)

Two incidents, both under 30 simulated shoppers, agent at AUTONOMY=act, qwen3:8b on the Mac.

| Incident | What the agent did | Outcome | Fix made after it |
|---|---|---|---|
| `20261020-201219-890e` | ShopErrors → 7 checks, 1 LLM call, 193 s: errors + `recent_change=changed` → **`rollout_undo:assistant`, p = 0.95**, OPA allowed, rolled back | verification 120 s later still read `error_rate=errors`, `latency=slow` → **reverted its correct fix** and escalated | error and latency checks read the last **1 min** (a 2-min window still held the pre-fix errors) |
| `20261020-201819-2d34` | same diagnosis and fix | `error_rate=ok` now, but `latency=slow` → reverted again | **per-service "slow"**: 2 s for juice-shop, 20 s for the assistant (an LLM endpoint: normal answers 4–14 s on the laptop); alert `AssistantSlow` > 30 s separate from `ShopSlow` |

Also seen: when the agent reverts a fix that had worked, the injector has already sealed the fault as
`resolved` (fixed_before_injector_heal). The incident file records `verification.recovered=false` and
`action.reverted`, so the scorer can tell; flagged for the RQ3 scorer.

The laptop could not carry two models + the cluster + load (a Kubernetes API call timed out), so the
final shakedown runs on AWS (Day 4), where latencies are also the real ones.
