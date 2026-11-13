# Fails-fast fix, verified on AWS, 21 Oct 2026 (afternoon; seen fault `assistant_bad_url` only)

Stockholm: ops t3.large and apps m7i.xlarge **on-demand**, GPU **g4dn.xlarge** (T4 16 GB, Spot; no g6
or g5 Spot capacity in any zone). Frozen rule v2, qwen3:8b agent / qwen3:4b-q4_K_M chat on the GPU,
AUTONOMY=act, 30 simulated users throughout. Code: 466b98d (the fix), cdc0227 (detached runs).
Files: `incidents-all.json` (all 4 incidents), `ground_truth.jsonl` (the sealed key).

**The fix** (see `../2026-10-21/README.md`, finding 1): the assistant now logs why an upstream call
failed (`upstream unreachable: ConnectError ...`, read by the RQ1 log check as `connection_errors`),
and the hypothesis prompt says a dependency that cannot be reached fails fast - errors at normal
latency - while slowness comes from saturation or queueing. The decision rule is unchanged.

| Round | Incident | Agent | Result |
|---|---|---|---|
| warm-up (no fault) | `143709-0339` AssistantBacklog as load started | H0 "no fault" 0.92, escalated, no action | correct: nothing to fix |
| control (6 min, no fault) | none | - | correct |
| 1 | `145459-f728` ShopErrors | `rollout_undo:assistant`, p = 0.98, acted, 6 checks | **fixed**, verified, 3.7 min inject to resolved |
| 2 | `150429-e90f` ShopErrors | same, p = 0.83, 14 checks (log check read `connection_errors`) | **fixed**, verified, 3.3 min |
| 3 | `151409-93df` ShopErrors | same, p = 0.93, 5 checks | **fixed**, verified, 3.4 min |

Before the fix (same night, same fault): 2 of 2 escalated (p = 0.76 and 0.075). After: 3 of 3 fixed,
no wrong actions, no reverted fixes. The model now predicts `latency=ok` for connection failures in
nearly every assistant hypothesis (round 3 still had three that predicted `slow`; the error and
recent-change readings outweighed them).

Caveat for RQ3: this changes the cluster agent's prompt and the demo app, not the frozen rule or the
RQ1 checks, and it was tuned on a seen fault only; the three held-out faults were not run.

Also verified: the deploy needed no manual step (GPU label, root, on-demand core), and long host
scripts now run detached (the first verification run lost its session at 20 min; the job itself
finished on the host and its data is above).

Cost: about 1 h 45 min of the three machines, roughly $0.70 of credits.
