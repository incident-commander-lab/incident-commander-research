"""The only actions the agent can take in the cluster. Each is reversible, returns an undo (or None
when there is nothing to undo), and is checked by OPA before it runs (cluster/agent/policy.rego).
The agent's Kubernetes RBAC allows exactly these verbs in the app namespaces and nothing else."""
from __future__ import annotations

import json
import subprocess
from typing import Callable, Optional

from . import probes
from .probes import KUBECTL, NAMESPACE

MAX_REPLICAS = 3
# remedy -> (reversible, description)
CATALOG = {
    "scale_out:juice-shop": (True, "Add one Juice Shop replica (max 3): more CPU for a traffic surge"),
    "scale_in:juice-shop": (True, "Remove one Juice Shop replica (min 1)"),
    "rollout_undo:juice-shop": (True, "Roll Juice Shop back to its previous release"),
    "rollout_undo:assistant": (True, "Roll the assistant back to its previous release"),
    "rate_limit:assistant": (True, "Halve how many questions the assistant sends to the model at once (queue the rest)"),
    "restart:juice-shop": (True, "Restart Juice Shop's pods (rolling)"),
    "restart:assistant": (True, "Restart the assistant's pods (rolling)"),
    "block_client": (True, "Temporarily block one client address at the ingress (always needs human approval)"),
    "none": (True, "No action"),
}
_PULSE = {   # Pulse in the cluster (testbed_ops/actions.py, in Kubernetes terms)
    "rollout_undo:worker": (True, "Roll the worker back to its previous release"),
    "rollout_undo:collector": (True, "Roll the collector back to its previous release"),
    "restart:worker": (True, "Restart the worker's pod (rolling)"),
    "restart:collector": (True, "Restart the collector's pod"),
    "restart:api": (True, "Restart the API's pods (rolling)"),
    "scale_out:api": (True, "Add one API replica (max 3); brings a scaled-down API back"),
    "kill_blocking_sessions:postgres": (True, "Terminate Postgres sessions that block others (their open transaction rolls back; no data is lost)"),
    "restore_redis_maxmemory:redis": (True, "Set Redis maxmemory back to its baseline (256 MB)"),
}
_SHOP = {k for k in CATALOG if k.split(":")[-1] in ("juice-shop", "assistant") or k == "block_client"}
if "pulse" in probes.APPS:
    CATALOG.update(_PULSE)
if "shop" not in probes.APPS:
    for k in _SHOP:
        CATALOG.pop(k)
REDIS_BASELINE = str(256 * 1024 * 1024)


def _k(*args: str) -> str:
    r = subprocess.run([KUBECTL, *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def _replicas(svc: str) -> int:
    return int(json.loads(_k("get", "deploy", svc, "-n", NAMESPACE[svc], "-o", "json"))["spec"]["replicas"])


def _scale(svc: str, n: int):
    _k("scale", "deploy", svc, "-n", NAMESPACE[svc], f"--replicas={n}")


def _env(svc: str) -> dict:
    d = json.loads(_k("get", "deploy", svc, "-n", NAMESPACE[svc], "-o", "json"))
    return {e["name"]: e.get("value") for e in d["spec"]["template"]["spec"]["containers"][0].get("env", [])}


def _apply(manifest: dict):
    r = subprocess.run([KUBECTL, "apply", "-f", "-"], input=json.dumps(manifest), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"kubectl apply: {r.stderr.strip()[:300]}")


def _block(ip: str, reason: str) -> Callable[[], None]:
    """Answer every request from one client address with 403 at the ingress: a Traefik route matching
    that client (above every other route) through an allow-list that admits nobody."""
    name = "ic-block-" + ip.replace(".", "-").replace(":", "-")
    meta = {"namespace": "shop", "labels": {"incident-commander": "block"},
            "annotations": {"incident-commander/reason": reason[:200]}}
    image = _k("get", "deploy", "traefik", "-n", "kube-system", "-o", "jsonpath={.spec.template.spec.containers[0].image}")
    allow = "ipWhiteList" if image.rsplit(":", 1)[-1].lstrip("v").startswith("2.") else "ipAllowList"   # renamed in Traefik 3
    _apply({"apiVersion": "traefik.io/v1alpha1", "kind": "Middleware", "metadata": {"name": "ic-deny-all", **meta},
            "spec": {allow: {"sourceRange": ["127.0.0.1/32"]}}})
    _apply({"apiVersion": "traefik.io/v1alpha1", "kind": "IngressRoute", "metadata": {"name": name, **meta},
            "spec": {"entryPoints": ["web"], "routes": [{
                "match": f"ClientIP(`{ip}`)", "kind": "Rule", "priority": 100000,
                "middlewares": [{"name": "ic-deny-all"}], "services": [{"name": "juice-shop", "port": 3000}]}]}})
    return lambda: _k("delete", "ingressroute.traefik.io", name, "-n", "shop", "--ignore-not-found")


def execute(remedy: str, reason: str) -> Optional[Callable[[], None]]:
    """Run a remedy. Returns a function that undoes it, or None."""
    kind, _, svc = remedy.partition(":")
    ns = NAMESPACE.get(svc, "")
    if kind == "kill_blocking_sessions":
        out = _k("exec", "-n", ns, "deploy/postgres", "--", "psql", "-U", "pulse", "-d", "pulse", "-tAc",
                 "SELECT pid, application_name, pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid IN "
                 "(SELECT unnest(pg_blocking_pids(pid)) FROM pg_stat_activity WHERE wait_event_type = 'Lock')")
        if not out.strip():
            raise RuntimeError("no session is blocking another")
        return None                    # a terminated session cannot be brought back (its transaction rolled back)
    if kind == "restore_redis_maxmemory":
        before = _k("exec", "-n", ns, "deploy/redis", "--", "redis-cli", "config", "get", "maxmemory").split()[-1]
        _k("exec", "-n", ns, "deploy/redis", "--", "redis-cli", "config", "set", "maxmemory", REDIS_BASELINE)
        return lambda: _k("exec", "-n", ns, "deploy/redis", "--", "redis-cli", "config", "set", "maxmemory", before)
    if kind == "scale_out":
        n = _replicas(svc)
        if n >= MAX_REPLICAS:
            raise RuntimeError(f"{svc} already at {n} replicas (max {MAX_REPLICAS})")
        _scale(svc, n + 1)
        return lambda: _scale(svc, n)
    if kind == "scale_in":
        n = _replicas(svc)
        if n <= 1:
            raise RuntimeError(f"{svc} already at 1 replica")
        _scale(svc, n - 1)
        return lambda: _scale(svc, n)
    if kind == "rollout_undo":
        _k("annotate", "deploy", svc, "-n", ns, f"kubernetes.io/change-cause=incident-commander: rollback ({reason})", "--overwrite")
        _k("rollout", "undo", "deploy", svc, "-n", ns)
        return lambda: _k("rollout", "undo", "deploy", svc, "-n", ns)     # undo of an undo = back to the release it replaced
    if kind == "rate_limit":
        before = _env(svc).get("MAX_CONCURRENCY", "4")
        _k("set", "env", "deploy", svc, "-n", ns, f"MAX_CONCURRENCY={max(1, int(before) // 2)}")
        return lambda: _k("set", "env", "deploy", svc, "-n", ns, f"MAX_CONCURRENCY={before}")
    if kind == "restart":
        _k("rollout", "restart", "deploy", svc, "-n", ns)
        return None
    if kind == "block_client":         # runs only after a human approved it (OPA)
        ip = probes.LAST_ATTACKER.get("ip")
        if not ip:
            raise RuntimeError("no attacking client identified (attack_signature has not read 'attack')")
        return _block(ip, f"{reason}: {probes.LAST_ATTACKER}")
    if kind == "none":
        return None
    raise ValueError(f"unknown remedy {remedy}")
