# Pulse at scale: the mentor-scale test bed

Spec and decisions: [`.plans/mentor-scale-testbed-spec.md`](../.plans/mentor-scale-testbed-spec.md).
Runs locally on k3d now; the same manifests go to k3s on three Stockholm Spot machines later.

```bash
cluster/cluster.sh up        # cluster + Prometheus/Grafana + dashboards + Juice Shop + AI assistant
cluster/cluster.sh ui        # Grafana http://localhost:3401 (dashboards "Cluster overview", "Shop and AI assistant")
cluster/cluster.sh load 60 5 # 60 simulated shoppers for 5 minutes, from inside the cluster (ops node)
cluster/cluster.sh stop      # pause (start resumes; down deletes)
```

| Part | Where |
|---|---|
| Node roles (ops / apps / gpu), ingress on :3400 | `k3d/cluster.yaml` |
| Prometheus (+ node-exporter, kube-state-metrics, Alertmanager), Grafana | `monitoring/*-values.yaml`, chart versions in `cluster.sh` |
| Dashboards (generated, every metric verified in the running cluster; GPU panels use NVIDIA DCGM names, "no data" until the AWS GPU) | `monitoring/make_dashboards.py` → `monitoring/dashboards/` |
| Juice Shop v20.3.0 (1 CPU per pod; sticky cookie for logged-in users, per-pod SQLite) + ingress | `k8s/shop.yaml` |
| AI assistant: "Ask about this product" → chat model; release = Deployment env (`NUM_CTX`, `MAX_CONCURRENCY`, `RETAIN_ANSWERS`) so `rollout undo` is a real fix | `assistant/` |
| Shopper journeys (Locust 2.33.0); `load trace` follows a real week of German Wikipedia hourly page views (CC0; one time zone, so a regional shop's daily cycle: night 9–13% of peak, evening peak), one real day per hour | `load/`, `load/traces/` |
| Fault injector with sealed answer key (same events as the Compose test bed, so `testbed_ops.score` works): `cpu_saturation_shop`, `memory_leak_assistant`, `gpu_memory_exhausted`; least-privilege RBAC | `chaos/`, `k8s/chaos.yaml` |

**Day 1 result (18 Oct, local):** 60 shoppers → ~22 req/s; Juice Shop at ~70% of its 1-CPU limit at the
peak; the 5-minute Locust summary: 4,890 requests, 2 failures (0.04%, assistant), but Juice Shop's p95
was already 6–10 s at ~16 req/s (median 0.6 s): on the Mac it shares CPU with the model answering the
assistant, so the real per-replica saturation point (planned ~100 req/s) is measured on AWS before the
peak load is set; the assistant's model (qwen3:1.7b on the Mac)
saturates at 4 in flight: answer p95 rises from ~4 s to ~14 s with a full queue. Locally all three
"nodes" share one Docker VM, so node CPU lines overlap; on AWS they are separate machines.

**Day 2 result (18 Oct, local):** each fault injected under light background load and fixed with the
command the agent will use; the answer key recorded every fix as "resolved, fixed before the injector
had to heal":

| Fault | What happened | Fix applied | Resolved after |
|---|---|---|---|
| `memory_leak_assistant` | a "routine release" in the rollout history; memory 211 → 249 MiB, then **OOMKilled** at the 256 MiB limit (~6 min) | `kubectl rollout undo` | 5.9 min |
| `gpu_memory_exhausted` | model reloaded with a 16k context, memory 1.9 → 3.3 GB; **no slowdown on the Mac** (ample unified memory, short prompts): the real symptom (VRAM overflow → CPU offload → slow answers) needs the L4 GPU | rate limit (`MAX_CONCURRENCY=2`, the accepted alternative) | 3.0 min |
| `cpu_saturation_shop` | +150 shoppers: Juice Shop at **97% of its CPU limit**, throttled in 21% of periods, p95 5 s, throughput fell | scale out to 2 replicas | 2.9 min |

Laptop impact: Docker peaked at 76% CPU for a few minutes during the surge.
**Safety note:** `cluster.sh` always passes `--context k3d-ic`: Docker Desktop resets the current kubectl
context to its own Kubernetes (which runs other projects).
