# Cluster shakedown on AWS, 21 Oct 2026 (Day 4; seen fault `assistant_bad_url` + GPU load test)

Stockholm, Spot: ops t3.large, GPU **g5.xlarge** (A10G 24 GB; no g6 Spot capacity in 1a or 1b), apps
m7i.xlarge. Frozen rule v2, qwen3:8b on the GPU (agent slot), qwen3:4b-q4_K_M chat (second time-slice),
AUTONOMY=act. Logs: `run1-*.log`, `run2-*.log`, `run3-*.log`; all four incident files: `incidents-all.json`.

## What happened to the session

- **The apps machine was reclaimed by Spot** (`instance-terminated-no-capacity`) at 00:54 UTC, during
  the deploy. Workaround for the night: juice-shop and the assistant were moved onto the GPU node
  (`kubectl patch ... nodeSelector role=gpu`). That patch is a Deployment rollout, which the agent
  later read as a "recent change" (see incidents 1 and 2).
- Deploy bugs, fixed in `infra/gpu.sh` / `infra/gpu-stack.yaml` (commit cd420c3): Session Manager logs
  in as `ssm-user` (scripts now run under `sudo -i`); a quiet deploy outlived the 20-min idle cut
  (heartbeat added); the device-plugin chart only schedules on nodes labelled
  `nvidia.com/gpu.present=true` (label added at join).

## GPU load test (120 users, 6 min, after the faults were healed)

| | |
|---|---|
| assistant `/assistant/ask` | 674 answers, median 1.9 s, p95 3.3 s, p99 4.1 s |
| errors | 3 of 14,150 requests (2 x 503, 1 x 504, all assistant) = 0.02 % |
| overall | ~39 req/s; product search median 1.0 s (shop shares the GPU node's 4 vCPU tonight) |

One A10G serves the chat model to 120 users with the agent model resident on the same GPU.

## Incidents

| Incident | Alert | Truth | Agent | Outcome |
|---|---|---|---|---|
| `012739-fc6d` | AssistantBacklog (load ramp, no fault) | none | `rollout_undo:juice-shop`, p = 0.97, acted | **wrong**: undid the node-move patch, pod went unschedulable; verification failed, agent reverted it |
| `013509-9813` | ShopSlow (120-user test, real overload) | `scale_out:juice-shop` | `rollout_undo:juice-shop`, p = 0.95, acted | **wrong** for the same reason; reverted |
| `014449-8d54` | ShopErrors, 3 min after inject | `rollout_undo:assistant` | right service, right fix, p = 0.76 < 0.78 | **escalated** with the correct proposal (11 s, 8 checks) |
| `015239-a9e9` | ShopErrors (stale events cleared, fresh agent) | `rollout_undo:assistant` | right fix proposed, p = 0.075 | **escalated**; H0 "no fault" took 0.90 |

## Findings (for the demo and RQ3)

1. **A bad URL fails fast.** Every fault hypothesis qwen3:8b wrote for the assistant predicted
   `latency=slow`; the real p95 was 0.1 s (connection refused returns at once). The one observation that
   contradicts every fault hypothesis but fits H0 decides it, and with more checks paid (15 vs 8) H0
   keeps collecting "ok" matches. On the laptop the same fault came with slow answers (p = 0.95).
   Rule v2 is frozen, so the agent's maths stays as is; a candidate for the demo is the
   probe side (a dead model address is `error_rate=errors` with `log_signature=connection_errors`, which
   the hypotheses do predict, so the log check is worth paying first). To decide, not done tonight.
2. **Any recent rollout makes rollback look certain.** Incidents 1 and 2 rolled back an operator's
   infrastructure patch with p > 0.95. On the planned cluster this cannot happen (no patches), but it is
   a real weakness worth a sentence in RQ3: `recent_change` does not tell a release from an infra edit.
3. Safety held: both wrong actions were caught by verification and reverted; both uncertain diagnoses
   escalated instead of acting. OPA allowed every proposed action (none was `block_client`).

Cost: ~1 h 15 min of the three machines, about $0.35 of credits.
