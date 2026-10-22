# Day 5 dry run on AWS, 22 Oct 2026

Stockholm: ops t3.large + apps m7i.xlarge on-demand, GPU **g5.xlarge** (A10G 24 GB, Spot; no g6 in 1a or
1b). Frozen rule v2, qwen3:8b agent and qwen3:4b-q4_K_M chat on the GPU, AUTONOMY=act. Traffic:
German Wikipedia's daily cycle, one day in one hour, 60 users at peak. Then the three **held-out**
faults and the seen one, each until the injector saw it fixed or its 15-min timeout, 4 min apart; then
120 users for 7 min. Code: c2a10bd. Files: `run.log`, `incidents-all.json` (8), `ground_truth.jsonl`,
`outbox.log` (the agent's messages). Video: `backup-video-2026-10-22.mp4` (not in git; 2 min 50 s,
10 s of session per frame, overlay redrawn from these logs).

## Faults

| Fault | Held out | Incidents (agent) | Outcome |
|---|---|---|---|
| `cpu_saturation_shop` (150-user surge) | yes | CPUSaturated: `scale_out:juice-shop` proposed at p = 0.16, H0 "no fault" 0.65 although `cpu_saturation=saturated` and `latency=slow` were read; escalated | **not fixed**; injector timeout after 15 min |
| `memory_leak_assistant` (release keeps every answer) | yes | 1st PodRestarting: `restart:assistant` p = 0.65, escalated. 2nd, 6 min later (`oom_killed`, `crash_backoff`, `recent_change=changed`): `rollout_undo:assistant` p = 0.99, acted, OPA allowed, verified | **fixed** by the agent, 11 min after inject |
| `gpu_memory_exhausted` (release raises the context to overflow the GPU) | yes | 1st AssistantBacklog: rollout p = 0.42 (`gpu_memory=full`, errors), escalated. 2nd: p = 0.63, escalated. 3rd ShopErrors: `rollout_undo:assistant` p = 0.96, acted, verified | **fixed** by the agent, 13 min after inject |
| `assistant_bad_url` | no (seen) | none | **invalid run**: the traffic trace had ended (60 min = 24 trace hours), no requests, nothing to notice. Fixed 3/3 on 21 Oct |

No wrong actions: both actions were the right fix and verified; the other six incidents escalated
(two of them correctly: load-driven backlog with no fault, H0 0.95 and 0.97).

**Held-out rule:** these results are not used to change the agent (RQ3 scores held-out faults; tuning
on them would leak the key). Known limitation to discuss with Nour, not to fix before scoring: with
the CPU surge the "no fault" hypothesis kept 0.65 against two contradicting readings; the same pattern
as the bad-url fault on 21 Oct (H0 collects matches from every healthy check).

## GPU load test (120 users, 7 min, g5)

| | |
|---|---|
| requests | 23,371, **0 failures** |
| assistant answers | 1,157; median 0.78 s, p95 1.4 s, p99 5.3 s |
| shop | p95 0.10 s; 56 req/s overall |
| GPU | 85 % busy, 11.6 GB of 24 GB used (both models resident) |

Better than 21 Oct (p95 3.3 s, 3 errors) because the shop is back on its own node.

## Fixed during the dry run

- Video overlay showed the agent's first message all session: `gpu.sh run` drops blank lines, which the
  recorder used to split messages. Split on timestamps now; this video's overlay was redrawn from the
  logs (phase and message times are exact; the frame clock is fitted from the old stamps, +-10 s).
- Grafana in the video used the browser's time zone; `timezone=utc` now matches the overlay.
- For the showing: traffic must run the whole session (`cluster/demo/demo.sh load 3`).

Cost: ~1 h 50 min of the three machines (g5 Spot + on-demand core), about $1.10 of credits.
