"""The only actions the agent can take in the cluster. Each is reversible, returns an undo (or None
when there is nothing to undo), and is checked by OPA before it runs (cluster/agent/policy.rego).
The agent's Kubernetes RBAC allows exactly these verbs in the app namespaces and nothing else."""
from __future__ import annotations

import json
import subprocess
from typing import Callable, Optional

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


def execute(remedy: str, reason: str) -> Optional[Callable[[], None]]:
    """Run a remedy. Returns a function that undoes it, or None."""
    kind, _, svc = remedy.partition(":")
    ns = NAMESPACE.get(svc, "")
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
    if kind == "block_client":
        raise RuntimeError("block_client is proposed to a human, never executed by the agent (week 2: ingress deny list)")
    if kind == "none":
        return None
    raise ValueError(f"unknown remedy {remedy}")
