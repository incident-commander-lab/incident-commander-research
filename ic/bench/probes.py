"""Generic probe templates for benchmark environments (Kubernetes microservices).

Live mode (ic/live/catalog.py) has three hand-wired observables for shop-svc.
A benchmark needs probes that work on ANY service, so each template here is
parameterised by (namespace, service) and does two things:

  * call(ns, svc)       → the action text the environment executes
                          (AIOpsLab's `exec_shell(...)` / `get_logs(...)` syntax)
  * label(output, svc)  → one discrete outcome label from `outcomes`

Discretising raw telemetry into labels is what lets the Bayesian layer in
ic/live/beliefs.py score probes by expected information gain. The thresholds
below are deliberately simple and stated in code so the paper can report them
(and run a sensitivity analysis on them).

`cost` is relative (1.0 ≈ one cheap kubectl read). Log reads are pricier
because their output is large.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

ERROR_RE = re.compile(
    r"\b(error|exception|panic|fatal|refused|timed? ?out|unavailable|failed|unauthori[sz]ed|denied)\b",
    re.IGNORECASE,
)
ERROR_LINE_THRESHOLD = 3  # ≥ this many matching log lines ⇒ "errors"


@dataclass(frozen=True)
class ProbeTemplate:
    name: str
    outcomes: tuple[str, ...]
    cost: float
    describe: str
    call: Callable[[str, str], str]
    label: Callable[[str, str], str]


@dataclass(frozen=True)
class ProbeInstance:
    template: ProbeTemplate
    service: str

    @property
    def key(self) -> str:
        return f"{self.template.name}:{self.service}"

    @property
    def outcomes(self) -> tuple[str, ...]:
        return self.template.outcomes

    @property
    def cost(self) -> float:
        return self.template.cost

    def call(self, namespace: str) -> str:
        return self.template.call(namespace, self.service)

    def label(self, output: str) -> str:
        return self.template.label(output, self.service)


def _lines_for(output: str, svc: str) -> list[str]:
    return [ln for ln in output.splitlines() if svc in ln]


# --------------------------------------------------------------------------- #
# Labelers — pure functions over raw text, unit-tested in tests/test_bench.py
# --------------------------------------------------------------------------- #
def label_pod_health(output: str, svc: str) -> str:
    """`kubectl get pods`: unhealthy if any matching pod is not fully ready,
    not Running, or has restarted. No matching pod at all is also unhealthy."""
    rows = _lines_for(output, svc)
    if not rows:
        return "unhealthy"
    for row in rows:
        cols = row.split()
        if len(cols) < 4:
            continue
        ready, status, restarts = cols[1], cols[2], cols[3]
        if "/" in ready:
            have, want = ready.split("/", 1)
            if have != want:
                return "unhealthy"
        if status != "Running":
            return "unhealthy"
        if restarts.isdigit() and int(restarts) > 0:
            return "unhealthy"
    return "healthy"


def label_log_errors(output: str, svc: str) -> str:
    if output.startswith("Error:"):  # AIOpsLab's "service does not exist" reply
        return "errors"
    hits = sum(1 for ln in output.splitlines() if ERROR_RE.search(ln))
    return "errors" if hits >= ERROR_LINE_THRESHOLD else "clean"


def label_endpoints(output: str, svc: str) -> str:
    """`kubectl get endpoints <svc>`: a Service whose selector or target port
    matches no ready pod shows <none>."""
    rows = _lines_for(output, svc)
    if not rows or "<none>" in rows[0] or "NotFound" in output:
        return "no_endpoints"
    return "has_endpoints"


def label_warning_events(output: str, svc: str) -> str:
    return "warnings" if _lines_for(output, svc) else "none"


def label_deployment(output: str, svc: str) -> str:
    """`kubectl get deploy <svc>`: READY a/b with a < b (incl. 0/0 scaled down)."""
    rows = _lines_for(output, svc)
    if not rows:
        return "degraded"
    cols = rows[0].split()
    if len(cols) >= 2 and "/" in cols[1]:
        have, want = cols[1].split("/", 1)
        if have != want or want == "0":
            return "degraded"
    return "ready"


TEMPLATES: dict[str, ProbeTemplate] = {t.name: t for t in (
    ProbeTemplate(
        "pod_health", ("unhealthy", "healthy"), 1.0,
        "Pods of the service are Running, fully ready, with zero restarts",
        lambda ns, s: f'exec_shell("kubectl get pods -n {ns} --no-headers | grep {s}")',
        label_pod_health),
    ProbeTemplate(
        "deployment_ready", ("degraded", "ready"), 1.0,
        "The service's Deployment has all desired replicas ready",
        lambda ns, s: f'exec_shell("kubectl get deploy {s} -n {ns} --no-headers")',
        label_deployment),
    ProbeTemplate(
        "endpoints", ("no_endpoints", "has_endpoints"), 1.0,
        "The Kubernetes Service routes to at least one ready pod",
        lambda ns, s: f'exec_shell("kubectl get endpoints {s} -n {ns} --no-headers")',
        label_endpoints),
    ProbeTemplate(
        "warning_events", ("warnings", "none"), 1.0,
        "Kubernetes Warning events mention the service",
        lambda ns, s: f'exec_shell("kubectl get events -n {ns} --field-selector type=Warning --no-headers | grep {s}")',
        label_warning_events),
    ProbeTemplate(
        "log_errors", ("errors", "clean"), 2.0,
        f"The service's logs contain ≥{ERROR_LINE_THRESHOLD} error-like lines",
        lambda ns, s: f'get_logs("{ns}", "{s}")',
        label_log_errors),
)}


def instances(services: list[str]) -> dict[str, ProbeInstance]:
    """Every (template, service) pair, keyed 'template:service'."""
    out = {}
    for svc in services:
        for t in TEMPLATES.values():
            p = ProbeInstance(t, svc)
            out[p.key] = p
    return out


def parse_services(kubectl_get_svc: str) -> list[str]:
    """Service names from `kubectl get svc -n <ns>` output (header optional)."""
    names = []
    for ln in kubectl_get_svc.splitlines():
        cols = ln.split()
        if not cols or cols[0] in ("NAME",) or cols[0].startswith("Error"):
            continue
        names.append(cols[0])
    return names
