# Spec: the mentor-scale test bed ("Pulse at scale")

Status: agreed in the interview of 15 Oct 2026. Owner: Shahesta + Claude (Nour stays on the RQ3
evaluation of the Compose sessions). First live showing to the mentor: **within ~1 week**.

## 1. Goal

A realistic, larger system on Kubernetes where the mentor can watch, live:
- real-trace-shaped users driving **CPU, RAM and GPU to 100%** on Grafana;
- faults happening **under load**, and the frozen Incident Commander agent diagnosing them and
  fixing or escalating, behind OPA, with verify/revert.

It is built to the RQ3 rules (sealed answer key, frozen decision rule, scored by the same scorer),
so every AWS hour can also produce paper data: **RQ3's second, larger setting**. The Compose test
bed stays as the first setting; its pilot sessions run now (after Nour approves the protocol
changes) and are not blocked by this work.

## 2. Decisions (from the interview, with the reason)

| Topic | Decision | Why |
|---|---|---|
| Purpose | Demo **and** RQ3 data | Every paid hour yields results; forces rigour (sealed key, frozen rule) |
| Platform | **k3s** on our own EC2 machines; locally **k3d** (k3s in Docker) | Real Kubernetes (pods, rollouts, scaling, kubelet/cAdvisor, DCGM) without EKS's $73/month; moving to EKS later is mostly config |
| Region / pricing | **eu-north-1**, all **Spot**, all nodes in **one AZ** (1a or 1b; g6 is not offered in 1c) | GPU quota is Spot-only there (4 vCPU); one AZ avoids cross-AZ data charges |
| GPU load | A **real AI feature**: "Ask about this product" chat, answered by an LLM on the GPU | Load tests then push the GPU to 100% for a real reason, and GPU faults are real incidents |
| Agent's LLM | **Same GPU, separate slot**: its own Ollama instance with `qwen3:8b` (the exact Q4_K_M artifact used in RQ1/RQ3), memory reserved; chat uses a second Ollama instance | Agent can't be starved of GPU memory by user load; model identical to every earlier experiment. Compute is still shared (reported as such) |
| Load generator | On a **separate node** (the ops node), never on the app node | Saturating the app must not starve Locust, Prometheus or the agent |
| Traffic | All four: (1) Locust users shaped by a **real public trace**; (2) **replay of real public HTTP logs**; (3) **labelled security dataset** replay; (4) the **live Wikipedia stream** (Pulse) | Realistic mix; (3) gives ground truth for security incidents |
| Time scale | **1 real day → 1 hour** (24×) | Each session sees several full daily cycles |
| Peak scale | **~200 users / ~100 req/s** | Saturates 1–2 Juice Shop replicas and the GPU chat on our small machines |
| New agent actions | scale out/in, rollout undo, rate-limit / shed load, **block client (human approval only)** | All reversible; OPA always denies autonomous blocking |
| Security incidents | Agent must **diagnose and escalate** (proposing a block); never block on its own | Acting on security is high-risk; tests "when not to act" |
| First new faults | CPU saturation, memory leak → OOM kill, GPU memory exhausted | Mentor's ask: spikes + faults under load |
| Checks | **Reuse the RQ1 Kubernetes catalog** (`ic/bench/probes.py`) + new metric checks | One catalog across RQ1 and RQ3: a strong point for the paper |
| Pulse | **Moves into the same cluster** (one cluster: Pulse + Juice Shop + assistant) | A multi-app company-like system; one agent watches everything |
| Access | **Nothing public, ever.** Session Manager port-forward on the laptop + screen share; Grafana snapshot afterwards | Juice Shop is deliberately vulnerable |
| Spot loss | Treat as a **natural incident**; mark the session interrupted; resume on a new Spot node | It is a real-world fault; on-demand GPU quota is 0 anyway |
| Local GPU | Locally the models run on the Mac (Ollama on host, CPU/Apple GPU); GPU panels show "no data"; GPU parts are proven in a ~1 h Stockholm shakedown (<$1) | Mac has no NVIDIA GPU |
| Cost | **$5 cap per session**, auto-stop, stack deleted at the end; $15 credit floor; out-of-pocket alert | Credits: $160 left |
| Laptop | No long runs at night on the laptop; long runs go to AWS | User's rule |

## 3. Architecture

```
                    ┌──────────────────────── k3s cluster (eu-north-1, one AZ, all Spot) ───────────────────────┐
 ops node           │ apps node (m7i.xlarge, 4 vCPU/16 GB)          gpu node (g6.xlarge, L4 24 GB)            │
 (t3.large,         │  ns shop:  juice-shop (1–3 replicas, 1 CPU    ns ai:  ollama-chat  (qwen3:4b, time-slice) │
  credit: standard) │            limit each), assistant (chat API)          ollama-agent (qwen3:8b Q4_K_M,      │
  k3s server        │  ns pulse: collector, worker, api, redis,                 reserved GPU memory)            │
  Prometheus +      │            postgres (ported from Compose)              dcgm-exporter                       │
  Grafana +         │  node-exporter, kubelet/cAdvisor                       node-exporter                       │
  Alertmanager      └───────────────────────────────────────────────────────────────────────────────────────────┘
  OPA, agent, chaos (sealed key), Locust + log/security replayers, kube-state-metrics
```

- **Why three nodes:** the apps node is the one we saturate; the ops node keeps monitoring, the
  agent and the load generator healthy; the GPU node is fixed by the quota (4 vCPU = one xlarge).
- **GPU sharing:** NVIDIA device plugin with **time-slicing (2 replicas)** so both Ollama pods get
  `nvidia.com/gpu: 1`. Memory separation via each Ollama's model size and `OLLAMA_NUM_PARALLEL`
  / context limits (L4 24 GB: agent 8B Q4 ≈ 6 GB + KV, chat 4B ≈ 3 GB + KV × parallel).
- **AMI:** AWS Deep Learning Base OSS NVIDIA Driver GPU AMI (Ubuntu 22.04) for the GPU node
  (driver + NVIDIA container toolkit preinstalled; k3s auto-detects the NVIDIA runtime); Ubuntu
  24.04 for the others. Looked up via SSM parameters (allowed by `ic-gpu-stockholm`).
- **Infra as code:** one CloudFormation stack `ic-gpu-<session>` (3 Spot instances, security group
  with **no inbound rules**, SSM role, idle-stop alarm, tags `project=incident-commander-gpu`),
  created per session and **deleted at the end**. k3s joins via a token passed in user data.
- **Agent permissions:** a Kubernetes ServiceAccount with RBAC limited to the app namespaces
  (get/list pods, logs, events, deployments; patch scale; rollout undo). OPA stays the gate.

### The AI feature ("assistant")

A small service we own (FastAPI or Node): `POST /assistant/ask {product_id, question}` → reads the
product from Juice Shop's API (`/api/Products/<id>`) → prompts the chat model → answers. Juice
Shop itself is **not forked**. Prometheus metrics: requests, latency histogram, queue depth,
errors, tokens/s. Releases via a ConfigMap-backed release file, like Pulse (so "bad release" and
rollback work the same way).

## 4. Traffic

| Source | What | Mapping onto our apps | Ground truth |
|---|---|---|---|
| (1) Locust users | Journeys: browse → search → product → ask assistant (≈10% of users) → basket → checkout | Rate follows **Wikipedia hourly page views** (public, CC0) for a fixed week, 24× compressed, scaled to peak ~200 users / ~100 req/s (Locust `LoadTestShape`) | n/a |
| (2) Public log replay | Timing and request mix from a public-domain web server access log | Path classes (static / listing / detail / search / errors) mapped to Juice Shop endpoints; original timing compressed like (1) | n/a |
| (3) Labelled security replay | A public, labelled dataset of normal vs malicious web requests (academic use) | Replayed in **bursts at scheduled times** (sealed in the answer key), against Juice Shop's own endpoints only, inside our private cluster | Labels + burst schedule → expected response: **escalate, propose block** |
| (4) Live stream | Wikipedia EventStreams (Pulse, unchanged) | Pulse pipeline | as today |

Dataset choice and licences are checked before use and written into `testbed/traffic/README.md`.
All replays target only our own cluster; nothing leaves it except Pulse's stream connection.

## 5. Faults (sealed answer key, same injector design as Compose)

| Fault | How (real, under load) | Correct response | Group |
|---|---|---|---|
| `cpu_saturation_shop` | Locust surge (2.5× the trace) on Juice Shop with 1 replica | `scale_out:juice-shop` | unseen |
| `memory_leak_assistant` | Assistant release that keeps every answer in memory | `rollout_undo:assistant` (OOMKilled → restarts) | unseen |
| `gpu_memory_exhausted` | Assistant config release raising context × parallel past the L4's memory → Ollama-chat errors | `rollout_undo:assistant` or `rate_limit:assistant` (both accepted, listed in the key) | unseen |
| `attack_burst` (week 2) | Labelled-malicious replay burst | escalate, propose `block_client` (needs approval) | security |
| Pulse's 9 faults (week 2) | Ported to Kubernetes (e.g. `upstream_cut` stays "do nothing") | as in Compose | seen / unseen as in Compose |
| Spot interruption | Natural | do nothing / escalate; session marked interrupted | natural |

Shakedowns of the new agent adapter use **seen faults only** (Pulse's `bad_release_worker`,
`db_lock`), so every new fault stays held out.

## 6. The agent in Kubernetes

- **Rule:** frozen rule v2 (`ic/bench/decide.py`), unchanged; decision-aware fix choice and the
  0.78 act threshold as in the Compose agent.
- **Checks:** the RQ1 catalog (`pod_health`, `deployment_ready`, `endpoints`, `service_ports`,
  `event_reason`, `log_signature`) via kubectl, **plus** PromQL checks: `cpu_saturation` (pod CPU vs
  limit), `memory_trend` (working set vs limit, OOMKilled count), `gpu_util`, `gpu_memory`,
  `latency_p95`, `error_rate`, `queue_depth`. New code imports `ic/bench`; it does **not** modify the
  code paths used by finished experiments.
- **Actions (OPA-gated, reversible):** `scale_out/in`, `rollout_undo`, `rate_limit` /
  `shed_load` (assistant concurrency), `block_client` (**OPA: approval required, always**), plus
  Pulse's existing ones ported.
- **Both agents:** the plain LLM agent (setup 2) gets the same Kubernetes checks and actions.
- Incident files keep today's shape, so `testbed_ops/score.py` scores cluster sessions unchanged.

## 7. Dashboards (Grafana, provisioned as code)

1. **Cluster overview:** CPU/RAM per node and per pod vs limits (node-exporter, cAdvisor,
   kube-state-metrics), GPU utilisation / memory / power / temperature (DCGM), pod restarts,
   OOMKilled.
2. **Shop & assistant:** users, req/s, p50/p95 latency, errors, assistant queue depth and tokens/s.
3. **Pulse:** today's dashboard.
4. **Incidents:** alert timeline with fault-injection annotations (revealed only after a session),
   agent decisions (act / escalate / OPA deny / revert).

## 8. Plan

**Week 1 (must-haves for the live showing: dashboards with CPU/RAM/GPU spikes; Juice Shop +
assistant under load; agent handling the 3 new faults live):**

| Day | Work | Done when |
|---|---|---|
| 1 | Local k3d cluster: Juice Shop, assistant, monitoring stack, dashboards 1–2 | Grafana shows pod CPU/RAM moving under a manual Locust run |
| 2 | Locust journeys + trace shaping (1); fault injector for the 3 faults with sealed key | A 1-hour local run shows the daily cycles; each fault injects and heals |
| 3 | Kubernetes check catalog + metric checks + actions + OPA; agent adapter; shakedown on seen faults only | Agent fixes a seen fault end to end locally |
| 4 | CloudFormation (3 Spot nodes, no inbound), k3s bootstrap, GPU time-slicing, DCGM, two Ollama pods; `gpu-session.sh up/down` with $5 cap and credit floor | 1-hour Stockholm shakedown (<$1): GPU at 100% on Grafana under chat load, stack deleted |
| 5 | Full dry-run session on AWS (~3 h, ~$1); fix what breaks; record a backup video | Report + screenshots + video |
| 6–7 | Buffer; live showing (screen share) | Mentor has seen it |

**Week 2:** security replay (3) + log replay (2); Pulse moved into the cluster with its 9 faults;
plain LLM agent on the cluster; RQ3 protocol section for the cluster setting (for Nour to review);
first scored cluster sessions.

## 9. Guardrails

- **No public endpoints, ever** (security group has no inbound rules; everything via Session
  Manager port-forwarding).
- **$5 per session** (estimated from live Spot prices before start, enforced by a timer that
  deletes the stack) and the **$15 credit floor** (`credit_guard`); the out-of-pocket budget alert
  stays on. Live prices on 15 Oct: g6.xlarge $0.16–0.18/h, m7i.xlarge $0.04/h, t3.large
  $0.016–0.020/h, EBS ≈ $0.02/h → **~$0.23–0.26/h for the whole stack** (~19–21 h per $5, ~$16–18
  for 70 h). t3.large runs with `CreditSpecification: standard` (no surprise CPU-credit charges).
- The IAM policy `ic-gpu-stockholm` already limits instance types, region and resources.
- Long runs never on the laptop at night.
- Nothing in this work changes `ic/bench` code used by the finished RQ1 and ladder results.

## 10. Risks and how we handle them

| Risk | Handling |
|---|---|
| Spot GPU unavailable / reclaimed | Try 1a then 1b; session marked interrupted, resumed on a new node; recorded backup for the showing |
| Two Ollama instances contend for GPU compute | Memory is separated, compute is not: measure agent latency with and without chat load and report it |
| Chat model too slow / too fast to show saturation | Tune model size (qwen3:1.7b / 4b) and assistant concurrency in the shakedown, then freeze before scored sessions |
| Juice Shop's CPU profile doesn't respond to scale-out | Verified on day 1 locally (single-threaded Node + per-replica CPU limit makes scale-out effective) |
| Ported/new checks behave differently from RQ1 | Same templates; new metric checks unit-tested; shakedown on seen faults only |
| Labelled dataset doesn't map well to Juice Shop | Choose the dataset in week 2 after a mapping check; security stays out of week 1 |
| One-week deadline | Week-1 must-haves only; everything else is week 2 |

## 11. Not in scope (for now)

EKS; on-demand GPU (quota 0); public dashboards; autonomous security blocking; multi-region;
changing the frozen decision rule.
