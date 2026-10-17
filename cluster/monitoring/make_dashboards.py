"""Generate the Grafana dashboards (JSON) from compact panel definitions.

    python3 cluster/monitoring/make_dashboards.py   # writes cluster/monitoring/dashboards/*.json

Every query uses metrics verified in the running cluster (cAdvisor, kube-state-metrics,
node-exporter, Traefik, the assistant) or NVIDIA DCGM's standard names (GPU node on AWS only;
'no data' locally).
"""
import json
import os

OUT = os.path.join(os.path.dirname(__file__), "dashboards")
APPS = 'namespace=~"shop|pulse|ai"'


def panel(title, exprs, unit="short", w=12, h=8, legend="{{pod}}", max_=None, desc=""):
    return {"type": "timeseries", "title": title, "description": desc, "datasource": {"type": "prometheus", "uid": "prom"},
            "gridPos": {"w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit, **({"max": max_, "min": 0} if max_ else {}),
                                         "custom": {"lineWidth": 2, "fillOpacity": 10}}, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
            "targets": [{"expr": e, "legendFormat": l, "refId": chr(65 + i)}
                        for i, (e, l) in enumerate(exprs if isinstance(exprs, list) else [(exprs, legend)])]}


def row(title):
    return {"type": "row", "title": title, "gridPos": {"w": 24, "h": 1}, "collapsed": False, "panels": []}


def layout(panels):
    x = y = rowh = 0
    for p in panels:
        w, h = p["gridPos"]["w"], p["gridPos"]["h"]
        if x + w > 24 or p["type"] == "row":
            x, y, rowh = 0, y + rowh, 0
        p["gridPos"].update(x=x, y=y)
        x, rowh = x + w, max(rowh, h)
        if p["type"] == "row":
            x, y, rowh = 0, y + h, 0
    for i, p in enumerate(panels, 1):
        p["id"] = i
    return panels


def dashboard(uid, title, panels):
    return {"uid": uid, "title": title, "timezone": "browser", "refresh": "10s", "schemaVersion": 39,
            "time": {"from": "now-30m", "to": "now"}, "tags": ["incident-commander"], "panels": layout(panels)}


overview = dashboard("ic-cluster", "Cluster overview: CPU, RAM, GPU", [
    row("Nodes"),
    panel("Node CPU busy", '100 * (1 - avg by (node) (rate(node_cpu_seconds_total{mode="idle"}[1m])))', "percent", legend="{{node}}", max_=100),
    panel("Node memory used", '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)', "percent", legend="{{node}}", max_=100),
    row("Pods (share of their own limit: 100% = saturated)"),
    panel("Pod CPU vs limit",
          f'100 * sum by (pod) (rate(container_cpu_usage_seconds_total{{{APPS},container!=""}}[1m])) '
          f'/ sum by (pod) (kube_pod_container_resource_limits{{{APPS},resource="cpu"}})', "percent"),
    panel("Pod memory vs limit",
          f'100 * sum by (pod) (container_memory_working_set_bytes{{{APPS},container!=""}}) '
          f'/ sum by (pod) (kube_pod_container_resource_limits{{{APPS},resource="memory"}})', "percent",
          desc="Above 100% the kernel kills the container (OOMKilled)"),
    panel("Restarts (last 10 min)", f'sum by (pod) (increase(kube_pod_container_status_restarts_total{{{APPS}}}[10m]))', w=8),
    panel("OOM kills", f'sum by (pod) (kube_pod_container_status_last_terminated_reason{{{APPS},reason="OOMKilled"}})', w=8),
    panel("Replicas available", f'kube_deployment_status_replicas_available{{{APPS}}}', legend="{{deployment}}", w=8),
    row("GPU (NVIDIA DCGM, AWS GPU node only)"),
    panel("GPU utilisation", 'avg by (gpu, Hostname) (DCGM_FI_DEV_GPU_UTIL)', "percent", legend="GPU {{gpu}}", max_=100, w=6),
    panel("GPU memory used", '100 * avg by (gpu) (DCGM_FI_DEV_FB_USED / (DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE))', "percent", legend="GPU {{gpu}}", max_=100, w=6),
    panel("GPU power", 'avg by (gpu) (DCGM_FI_DEV_POWER_USAGE)', "watt", legend="GPU {{gpu}}", w=6),
    panel("GPU temperature", 'avg by (gpu) (DCGM_FI_DEV_GPU_TEMP)', "celsius", legend="GPU {{gpu}}", w=6),
])

shop = dashboard("ic-shop", "Shop and AI assistant", [
    row("What users see (measured at the ingress)"),
    panel("Requests per second", 'sum by (service) (rate(traefik_service_requests_total{service=~"shop-.*"}[1m]))', "reqps", legend="{{service}}", w=8),
    panel("Latency p95", 'histogram_quantile(0.95, sum by (le, service) (rate(traefik_service_request_duration_seconds_bucket{service=~"shop-.*"}[1m])))', "s", legend="{{service}}", w=8),
    panel("Error rate (5xx)", 'sum by (service) (rate(traefik_service_requests_total{service=~"shop-.*",code=~"5.."}[1m])) / sum by (service) (rate(traefik_service_requests_total{service=~"shop-.*"}[1m]))', "percentunit", legend="{{service}}", w=8),
    row("AI assistant (the GPU's workload)"),
    panel("Answer time p95", 'histogram_quantile(0.95, sum by (le) (rate(assistant_request_seconds_bucket[1m])))', "s", legend="p95", w=6),
    panel("Queue and in flight", [("sum(assistant_queue_depth)", "waiting"), ("sum(assistant_inflight)", "at the model")], w=6),
    panel("Tokens per second", "sum(rate(assistant_tokens_total[1m]))", legend="tokens/s", w=6),
    panel("Answers by outcome", 'sum by (status) (rate(assistant_requests_total[1m]))', "reqps", legend="{{status}}", w=6),
    panel("Memory kept by the assistant", "sum(assistant_retained_bytes)", "bytes", legend="retained", w=8,
          desc="Grows only under the memory-leak release"),
    panel("Orders placed", "sum(rate(juiceshop_orders_placed_total[5m])) * 60", legend="orders/min", w=8),
    panel("Running release", 'max by (version, num_ctx, max_concurrency, retain) (assistant_release_info)', legend="v{{version}} ctx={{num_ctx}} conc={{max_concurrency}} retain={{retain}}", w=8),
])

os.makedirs(OUT, exist_ok=True)
for d in (overview, shop):
    with open(os.path.join(OUT, f"{d['uid']}.json"), "w") as f:
        json.dump(d, f, indent=1)
print("written:", [d["uid"] for d in (overview, shop)])
