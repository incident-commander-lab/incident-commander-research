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
| Shopper journeys (Locust 2.33.0); trace shaping comes on day 2 | `load/` |

**Day 1 result (18 Oct, local):** 60 shoppers → ~22 req/s; Juice Shop at ~70% of its 1-CPU limit at the
peak (so ~100 req/s saturates one replica, as planned); the assistant's model (qwen3:1.7b on the Mac)
saturates at 4 in flight: answer p95 rises from ~4 s to ~14 s with a full queue. Locally all three
"nodes" share one Docker VM, so node CPU lines overlap; on AWS they are separate machines.
