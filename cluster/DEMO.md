# Live showing runbook (mentor, screen share)

About 25 minutes of showing on a session started an hour before. Everything runs on AWS; the laptop
only opens tunnels. Rehearsed on 22 Oct (`shakedown/2026-10-22-dryrun/`).

## T-60 min: start

```bash
infra/gpu.sh up 3          # 3 h session, self-terminates; GPU Spot is scarce in Stockholm, so start early
infra/gpu.sh deploy        # ~20 min
cluster/demo/demo.sh load 3
infra/gpu.sh ui            # Grafana http://localhost:3401, shop http://localhost:3400
```

If no GPU is found after all 7 tries (it happened for g6 every day this week; g5 and g4dn worked), or
anything breaks: show the backup video (`backup-video-2026-10-22.mp4`) and the incident files.

Open in the browser, Grafana in kiosk mode:
- Shop and AI assistant: http://localhost:3401/d/ic-shop?kiosk&timezone=utc
- Cluster: http://localhost:3401/d/ic-cluster?kiosk&timezone=utc
- The shop itself: http://localhost:3400 (ask the assistant about a product)

## The showing (~25 min)

| min | Show | Say |
|---|---|---|
| 0-3 | Architecture slide; the shop; ask the assistant a question | Real shop (Juice Shop), real AI feature on a GPU, real users' daily traffic replayed |
| 3-6 | `ic-shop` dashboard: traffic following the day, assistant answer times, GPU | One GPU serves the chat model and the agent's model side by side |
| 6-14 | `demo.sh inject memory_leak_assistant`; `ic-cluster` memory climbs to the limit, OOM kills; `demo.sh feed` | The agent is not told. It notices, checks, and rolls back the bad release once it is sure (rehearsal: 11 min, first escalation then fix) |
| 14-20 | `demo.sh inject gpu_memory_exhausted`; GPU memory full, backlog, errors; `demo.sh feed` | When unsure it escalates with its evidence and a proposed fix; `demo.sh approve <id>` is the human in the loop |
| 20-25 | `ic-cluster` replicas, OPA policy (`cluster/agent/policy.rego`), Q&A | Every action passes the policy engine; blocking clients always needs a human; every fix is verified and reverted if it does not hold |

Faults take 10-15 min to resolve end to end, so talk over them or inject the second one while the first
is being handled. `demo.sh heal` ends a fault at once if time runs short.

Not in the showing: `cpu_saturation_shop` (escalated, not fixed, in the rehearsal; an honest limitation
to mention if asked) and anything that would tune the agent on held-out faults.

## After

```bash
infra/gpu.sh down          # or let it self-terminate
```
