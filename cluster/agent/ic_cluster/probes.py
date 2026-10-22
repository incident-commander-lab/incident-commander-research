"""The agent's checks in the cluster, in the benchmark's probe form so the frozen rule
(ic/bench/decide.py) runs on them unchanged.

* The six RQ1 Kubernetes checks (ic/bench/probes.py) are reused AS-IS: the same kubectl commands
  (taken from their AIOpsLab `exec_shell(...)` / `get_logs(...)` call text) and the same labellers.
  RQ1 and the cluster therefore share one catalog.
* Metric checks read Prometheus: CPU and memory against the pod's limit, latency and errors at the
  ingress (what users see), the assistant's queue, GPU (DCGM, AWS only) and recent releases.

A check that cannot be read (tool error, no data) returns label None: the rule marks it
unavailable (catalog v2: a failed read is not evidence).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Optional

import httpx

from ic.bench import probes as bench
from ic.bench.probes import ProbeInstance, ProbeTemplate

PROM = os.environ.get("PROMETHEUS_URL", "http://prometheus-server.monitoring.svc")
KUBECTL = os.environ.get("KUBECTL", "kubectl")

# service -> namespace, and the ingress service name Traefik reports for it
NAMESPACE = {"juice-shop": "shop", "assistant": "shop"}
INGRESS = {"juice-shop": "shop-juice-shop-3000@kubernetes", "assistant": "shop-assistant-8000@kubernetes"}
USES_GPU = {"assistant"}
# "slow" thresholds per service: shop pages should answer in well under a second; the assistant is an
# LLM endpoint whose NORMAL answers take seconds (4-14 s on a laptop in the 20 Oct shakedown), so a
# shared 2 s threshold read it as permanently slow and failed a correct fix's verification.
SLOW_S = {"juice-shop": 2.0, "assistant": 20.0}


def _sh(cmd: str, timeout: int = 30) -> str:
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
    return (r.stdout + (r.stderr if r.returncode not in (0, 1) else "")).strip()   # grep: 1 = no match


def run_bench_call(call: str, ns: str, svc: str) -> str:
    """Execute an RQ1 probe's AIOpsLab call text the way AIOpsLab would."""
    m = re.fullmatch(r'exec_shell\("(.*)"\)', call, re.S)
    if m:
        return _sh(m.group(1).replace('\\"', '"'))
    if call.startswith("get_logs("):
        return _sh(f"{KUBECTL} logs -n {ns} deploy/{svc} --tail=200 --all-containers 2>&1")
    raise ValueError(f"unknown call form: {call[:60]}")


def prom(query: str) -> Optional[float]:
    """Scalar (sum of the vector) or None when there is no data."""
    r = httpx.get(f"{PROM}/api/v1/query", params={"query": query}, timeout=10)
    r.raise_for_status()
    res = r.json()["data"]["result"]
    return sum(float(x["value"][1]) for x in res) if res else None


def _metric(name, outcomes, describe, read, label, eps=0.05, miss=None, cost=1.0):
    return ProbeTemplate(name, outcomes, cost, describe, call=lambda ns, s, n=name: f"{n}:{s}",
                         label=label, eps=eps, miss=miss), read


def _q_ratio(num: str, den: str) -> str:
    return f"({num}) / ({den})"


def _read_cpu(s):
    ns = NAMESPACE[s]
    v = prom(f'max_over_time(({_q_ratio(f"sum(rate(container_cpu_usage_seconds_total{{namespace=\"{ns}\",container=\"{s}\"}}[1m]))", f"sum(kube_pod_container_resource_limits{{namespace=\"{ns}\",container=\"{s}\",resource=\"cpu\"}})")})[3m:15s])')
    return json.dumps({"cpu_vs_limit_max_3m": v})


def _read_mem(s):
    ns = NAMESPACE[s]
    v = prom(f'max({_q_ratio(f"max by (pod) (container_memory_working_set_bytes{{namespace=\"{ns}\",container=\"{s}\"}})", f"max by (pod) (kube_pod_container_resource_limits{{namespace=\"{ns}\",container=\"{s}\",resource=\"memory\"}})")})')
    oom = prom(f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{ns}",container="{s}"}}[15m])'
               f' * on (namespace, pod, container) group_left max by (namespace, pod, container) '
               f'(kube_pod_container_status_last_terminated_reason{{namespace="{ns}",container="{s}",reason="OOMKilled"}}))')
    return json.dumps({"memory_vs_limit": v, "oom_restarts_15m": oom})


# Latency and errors use 1-minute windows: verification reads them VERIFY_SECONDS (120 s) after a fix,
# and a 2-minute window would still contain the errors from before the fix (cluster shakedown, 20 Oct:
# a correct rollback was judged "did not hold" and reverted).
def _read_latency(s):
    v = prom(f'histogram_quantile(0.95, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{{service="{INGRESS[s]}"}}[1m])))')
    return json.dumps({"p95_s": v})


def _read_errors(s):
    tot = prom(f'sum(rate(traefik_service_requests_total{{service="{INGRESS[s]}"}}[1m]))')
    err = prom(f'sum(rate(traefik_service_requests_total{{service="{INGRESS[s]}",code=~"5.."}}[1m]))')
    return json.dumps({"rps": tot, "err_rps": err or 0.0})


def _read_queue(_s):
    return json.dumps({"queue_avg_2m": prom("avg_over_time(sum(assistant_queue_depth)[2m:15s])")})


def _read_gpu_mem(_s):
    return json.dumps({"fb_used_share": prom("max(DCGM_FI_DEV_FB_USED / (DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE))")})


def _read_gpu_util(_s):
    return json.dumps({"util_avg_2m": prom("max(avg_over_time(DCGM_FI_DEV_GPU_UTIL[2m]))")})


def _read_change(s):
    """Newest ReplicaSet of the Deployment created in the last 30 minutes, beyond revision 1."""
    out = _sh(f"{KUBECTL} get rs -n {NAMESPACE[s]} -l app={s} -o json")
    items = json.loads(out).get("items", []) if out.startswith("{") else []
    revs = sorted(((int(i["metadata"]["annotations"].get("deployment.kubernetes.io/revision", "0")),
                    i["metadata"]["creationTimestamp"]) for i in items), reverse=True)
    return json.dumps({"revisions": revs[:3], "now": _sh("date -u +%Y-%m-%dT%H:%M:%SZ")})


def _num(out: str, key: str) -> Optional[float]:
    v = json.loads(out).get(key)
    return None if v is None else float(v)


def _lbl_cpu(out, _s):
    v = _num(out, "cpu_vs_limit_max_3m")
    return None if v is None else ("saturated" if v >= 0.9 else "ok")


def _lbl_mem(out, _s):
    d = json.loads(out)
    if d.get("oom_restarts_15m"):
        return "oom_killed"
    v = d.get("memory_vs_limit")
    return None if v is None else ("near_limit" if v >= 0.85 else "ok")


def _lbl_latency(out, s):
    v = _num(out, "p95_s")
    return "ok" if v is None or v != v else ("slow" if v > SLOW_S.get(s, 2.0) else "ok")   # no traffic: nothing slow


def _lbl_errors(out, _s):
    d = json.loads(out)
    return "errors" if d.get("rps") and (d.get("err_rps") or 0) / d["rps"] > 0.05 else "ok"


def _lbl_queue(out, _s):
    v = _num(out, "queue_avg_2m")
    return None if v is None else ("backlog" if v >= 0.5 else "ok")


def _lbl_gpu_mem(out, _s):
    v = _num(out, "fb_used_share")
    return None if v is None else ("full" if v >= 0.9 else "ok")


def _lbl_gpu_util(out, _s):
    v = _num(out, "util_avg_2m")
    return None if v is None else ("saturated" if v >= 90 else "ok")


def _lbl_change(out, _s):
    from datetime import datetime
    d = json.loads(out)
    if not d["revisions"]:
        return None
    rev, created = d["revisions"][0]
    age = (datetime.fromisoformat(d["now"].replace("Z", "+00:00"))
           - datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds()
    return "changed" if rev > 1 and age < 1800 else "unchanged"


# Attack patterns in a request line (URL-encoded or not): SQL injection, XSS, path traversal, command injection
ATTACK_RX = re.compile(r"union(\s|%20|\+)+(all(\s|%20|\+)+)?select|(%27|')(\s|%20|\+)*(or|and)(\s|%20|\+)|(%27|')(\s|%20)*--|"
                       r"<script|%3cscript|onerror|onload=|javascript:|\.\./|\.\.%2f|%2e%2e|\.\.\\|%5c\.\.|/etc/passwd|win\.ini|"
                       r"(;|%3b|\||%7c)(\s|%20)*(cat|id|ls|uname|whoami)\b|\$\(|%24%28|`", re.I)
ATTACK_MIN, ATTACK_SHARE = 20, 0.3
LAST_ATTACKER: dict = {}    # the latest attacking client seen, for block_client (actions.py)
# Traefik common log: <client> - - [date] "METHOD path PROTO" status size "ref" "ua" n "router" "url" Nms
_ACCESS = re.compile(r'^(\S+) \S+ \S+ \[[^\]]*\] "(?:\S+) ([^"]*?) HTTP/[^"]*" (\d{3}) .*?"([^"]*)" "[^"]*" \d+ "([^"]*)"')


def _read_attack(_s):
    out = _sh(f"{KUBECTL} logs -n kube-system deploy/traefik --since=2m --tail=30000", timeout=60)
    total, hits, lines = {}, {}, 0
    for ln in out.splitlines():
        m = _ACCESS.match(ln)
        if not m or "ic-block" in m.group(5):     # requests our own block answered are not evidence of a new attack
            continue
        lines += 1
        ip = m.group(1)
        total[ip] = total.get(ip, 0) + 1
        if ATTACK_RX.search(m.group(2)):
            hits[ip] = hits.get(ip, 0) + 1
    top = max(hits, key=hits.get) if hits else None
    return json.dumps({"lines": lines, "clients": len(total), "top_client": top,
                       "attack_requests": hits.get(top, 0) if top else 0,
                       "share": round(hits[top] / total[top], 3) if top else 0.0})


def _lbl_attack(out, _s):
    d = json.loads(out)
    if not d["lines"]:
        return None                                 # no access log: unreadable, not "clean"
    if d["top_client"] and d["attack_requests"] >= ATTACK_MIN and d["share"] >= ATTACK_SHARE:
        LAST_ATTACKER.update(ip=d["top_client"], requests=d["attack_requests"], share=d["share"])
        return "attack"
    return "clean"


_METRIC = {
    "cpu_saturation": _metric("cpu_saturation", ("saturated", "ok"),
                              "CPU use against the pod's CPU limit, peak of the last 3 minutes (saturated >= 90%)", _read_cpu, _lbl_cpu),
    "memory_pressure": _metric("memory_pressure", ("oom_killed", "near_limit", "ok"),
                               "Memory against the pod's limit, and OOM kills in the last 15 minutes", _read_mem, _lbl_mem),
    "latency": _metric("latency", ("slow", "ok"), "p95 response time users see at the ingress over the last minute (slow: > 2 s for juice-shop, > 20 s for the assistant)",
                       _read_latency, _lbl_latency),
    "error_rate": _metric("error_rate", ("errors", "ok"), "Share of 5xx responses at the ingress over the last minute (errors > 5%)",
                          _read_errors, _lbl_errors),
    "recent_change": _metric("recent_change", ("changed", "unchanged"),
                             "A new release (rollout) of this Deployment in the last 30 minutes", _read_change, _lbl_change, eps=0.02),
    "queue_depth": _metric("queue_depth", ("backlog", "ok"), "Questions waiting for a model slot (2-minute average)",
                           _read_queue, _lbl_queue),
    "gpu_memory": _metric("gpu_memory", ("full", "ok"), "GPU memory in use (full >= 90%; DCGM)", _read_gpu_mem, _lbl_gpu_mem),
    "gpu_util": _metric("gpu_util", ("saturated", "ok"), "GPU utilisation, 2-minute average (saturated >= 90%; DCGM)",
                        _read_gpu_util, _lbl_gpu_util),
    "attack_signature": _metric("attack_signature", ("attack", "clean"),
                                "Requests at the ingress in the last 2 minutes carrying attack patterns (SQL injection, XSS, path "
                                f"traversal, command injection) from one client (attack: >= {ATTACK_MIN} such requests and >= "
                                f"{int(ATTACK_SHARE * 100)}% of that client's requests)", _read_attack, _lbl_attack, eps=0.1, cost=2.0),
}

KUBE = list(bench.TEMPLATES)                       # pod_health, deployment_ready, endpoints, service_ports, event_reason, log_signature
APPLIES = {
    "juice-shop": KUBE + ["cpu_saturation", "memory_pressure", "latency", "error_rate", "recent_change", "attack_signature"],
    "assistant": KUBE + ["cpu_saturation", "memory_pressure", "latency", "error_rate", "recent_change",
                         "queue_depth", "gpu_memory", "gpu_util"],
}
TEMPLATES = {**bench.TEMPLATES, **{n: t for n, (t, _) in _METRIC.items()}}


def instances() -> dict[str, ProbeInstance]:
    return {f"{n}:{s}": ProbeInstance(TEMPLATES[n], s) for s, names in APPLIES.items() for n in names}


def run(probe: ProbeInstance) -> tuple[str, Optional[str]]:
    """(raw output, label); label None = could not be read (unavailable, not evidence)."""
    name, svc = probe.template.name, probe.service
    try:
        if name in _METRIC:
            raw = _METRIC[name][1](svc)
        else:
            raw = run_bench_call(probe.call(NAMESPACE[svc]), NAMESPACE[svc], svc)
            if bench.tool_error(raw) or raw.startswith("error:") or "Unable to connect" in raw:
                return raw, None
        return raw, probe.label(raw)
    except Exception as e:   # tool failure is not evidence (catalog v2 rule)
        return f"ERROR: {type(e).__name__}: {e}"[:400], None
