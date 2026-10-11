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

`eps` is each template's measurement-error rate for the Bayesian update. A
kubectl state read is rarely wrong; a regex over free-form logs often is, so one
ambiguous log line cannot swing the posterior on its own.

The last outcome of every template is its healthy reading (the no-fault
hypothesis relies on that).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

# Log signatures — which KIND of failure a service's logs show. Patterns are
# generic SRE vocabulary (not tuned to any benchmark problem). Order matters
# only for ties: earlier categories win.
LOG_SIGNATURES: dict[str, re.Pattern] = {
    "auth_errors": re.compile(
        r"unauthori[sz]ed|authenticat\w* fail|auth\w* error|permission denied|access denied|"
        r"not authorized|invalid (credentials|password|token)|forbidden|\b401\b|\b403\b", re.I),
    "config_errors": re.compile(
        r"config\w* (error|invalid|missing|not found)|missing (env|environment|config|argument|flag)|"
        r"invalid (argument|option|value|flag)|unknown (flag|option)|no such file|cannot parse|"
        r"failed to (load|parse|read) config|exec format error|command not found", re.I),
    "connection_errors": re.compile(
        r"connection refused|connection reset|no route to host|i/o timeout|dial tcp|"
        r"timed? ?out|deadline exceeded|unreachable|no such host|broken pipe|\bEOF\b", re.I),
    "storage_errors": re.compile(
        r"no space left|disk (full|error|quota)|read-only file system|i/o error|"
        r"(failed to|cannot|unable to) mount|mount (failed|error)|volume .*(not found|failed|missing)|"
        r"database .* (not found|does not exist)|user .* not found", re.I),
    "crash_errors": re.compile(
        r"panic|segmentation fault|sigsegv|traceback|stack trace|nil pointer|null ?pointer|"
        r"fatal error|uncaught exception|core dumped", re.I),
}
LOG_SIGNATURE_MIN_LINES = 2  # a category needs ≥ this many matching lines
LOG_RECENT_LINES = 50        # judge the current state, not start-up chatter

# Kubernetes event reasons → coarse category (`kubectl get events` REASON column).
EVENT_REASONS: dict[str, tuple[str, ...]] = {
    "scheduling": ("FailedScheduling", "NotTriggerScaleUp"),
    "storage": ("FailedMount", "FailedAttachVolume", "ProvisioningFailed", "FailedBinding"),
    "image": ("Failed", "ErrImagePull", "ImagePullBackOff", "InspectFailed"),
    "probe_failure": ("Unhealthy", "ProbeWarning"),
    "crash_backoff": ("BackOff", "OOMKilling", "Killing", "Evicted"),
}


@dataclass(frozen=True)
class ProbeTemplate:
    name: str
    outcomes: tuple[str, ...]
    cost: float
    describe: str
    call: Callable[[str, str], str]
    label: Callable[[str, str], str]
    eps: float = 0.05  # P(label contradicts the truth): low for kubectl state, high for log regexes
    risk: float = 0.0  # every benchmark probe is a read; interventions (Gap 2) set this
    miss: Optional[float] = None  # P(reads healthy | a symptom was predicted): silent faults. None → eps


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


_HASH = re.compile(r"^[a-z0-9]{5,10}$")


def pod_owner(pod: str) -> str:
    """Workload name of a pod: 'geo-c47ff745-5bgdz' → 'geo' (Deployment: drop the
    ReplicaSet hash and pod suffix), 'mongodb-geo-0' → 'mongodb-geo' (StatefulSet)."""
    pod = pod.split("/", 1)[-1]  # events print 'pod/<name>'
    parts = pod.split("-")
    if len(parts) >= 3 and _HASH.match(parts[-2]) and _HASH.match(parts[-1]):
        return "-".join(parts[:-2])
    if len(parts) >= 2 and parts[-1].isdigit():
        return "-".join(parts[:-1])
    return pod


def _lines_for(output: str, svc: str) -> list[str]:
    """Lines about exactly this service. A plain substring test would let 'geo'
    match 'mongodb-geo', and 'user' match 'user-timeline-service'."""
    rows = []
    for ln in output.splitlines():
        for tok in ln.split():
            if tok == svc or pod_owner(tok) == svc:
                rows.append(ln)
                break
    return rows


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


def label_log_signature(output: str, svc: str) -> str:
    """The failure category with the most matching log lines (≥ the minimum),
    or 'clean'. An AIOpsLab 'service does not exist' reply counts as a crash."""
    if output.startswith("Error:"):
        return "crash_errors"
    # Only the recent tail: services log transient errors while their dependencies
    # start (frontend "connection refused" to consul in the pilot's no-fault run).
    lines = output.splitlines()[-LOG_RECENT_LINES:]
    best, best_hits = "clean", LOG_SIGNATURE_MIN_LINES - 1
    for category, pattern in LOG_SIGNATURES.items():
        hits = sum(1 for ln in lines if pattern.search(ln))
        if hits > best_hits:
            best, best_hits = category, hits
    return best


def label_endpoints(output: str, svc: str) -> str:
    """`kubectl get endpoints <svc>`: a Service whose selector or target port
    matches no ready pod shows <none>."""
    rows = _lines_for(output, svc)
    if not rows or "<none>" in rows[0] or "NotFound" in output:
        return "no_endpoints"
    return "has_endpoints"


def label_event_reason(output: str, svc: str) -> str:
    """Most specific Warning-event category mentioning the service, or 'none'.
    Checked in EVENT_REASONS order, so scheduling/storage beat generic backoff."""
    rows = _lines_for(output, svc)
    reasons = {tok for row in rows for tok in row.split()}
    for category, names in EVENT_REASONS.items():
        if reasons & set(names):
            return category
    return "none"


def label_service_ports(output: str, svc: str) -> str:
    """Service targetPort vs the containerPorts its pods declare. Output shape:
        T <targetPort,...>              (first line, from `kubectl get svc`)
        <pod-name> <containerPort,...>  (one line per pod)
    A numeric targetPort that no owned pod declares is a mismatch: traffic is sent
    to a port nothing listens on, while endpoints still look healthy. Named target
    ports, or pods that declare no ports, give no evidence (ports_ok)."""
    targets: set[str] = set()
    pod_ports: set[str] = set()
    for line in output.splitlines():
        cols = line.split()
        if not cols:
            continue
        if cols[0] == "T":
            targets = {p for c in cols[1:] for p in c.split(",") if p.isdigit()}
        elif len(cols) >= 2 and pod_owner(cols[0]) == svc:
            pod_ports |= {p for p in cols[1].split(",") if p.isdigit()}
    if targets and pod_ports and not targets & pod_ports:
        return "port_mismatch"
    return "ports_ok"


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
        "service_ports", ("port_mismatch", "ports_ok"), 1.0,
        "The Service's targetPort matches a port the service's pods actually declare "
        "(a mismatch silently drops traffic while endpoints still look healthy)",
        lambda ns, s: (f"exec_shell(\"echo T $(kubectl get svc {s} -n {ns} --no-headers "
                       f"-o custom-columns='T:.spec.ports[*].targetPort'); "
                       f"kubectl get pods -n {ns} --no-headers "
                       f"-o custom-columns='N:.metadata.name,P:.spec.containers[*].ports[*].containerPort' "
                       f"| grep {s}\")"),
        label_service_ports),
    ProbeTemplate(
        "event_reason", ("scheduling", "storage", "image", "probe_failure", "crash_backoff", "none"), 1.0,
        "Kind of Kubernetes Warning event for the service: scheduling (pod cannot be placed), "
        "storage (volume mount/bind), image (pull failure), probe_failure (liveness/readiness), "
        "crash_backoff (restarts, OOM, eviction), none",
        lambda ns, s: f'exec_shell("kubectl get events -n {ns} --field-selector type=Warning --no-headers | grep {s}")',
        label_event_reason, eps=0.10, miss=0.30),
    ProbeTemplate(
        "log_signature", ("auth_errors", "config_errors", "connection_errors", "storage_errors",
                          "crash_errors", "clean"), 2.0,
        "Kind of error dominating the service's logs: auth_errors (credentials/permissions), "
        "config_errors (bad/missing config, args, binary), connection_errors (cannot reach a "
        "dependency), storage_errors (disk/volume/database objects), crash_errors (panics, "
        "stack traces), clean",
        lambda ns, s: f'get_logs("{ns}", "{s}")',
        label_log_signature, eps=0.25, miss=0.40),
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
    """Service names from `kubectl get svc -n <ns>` output. Only real table rows
    count: NAME TYPE CLUSTER-IP ... where TYPE is a Service type. (AIOpsLab prefixes
    its first reply with 'Please take the next action', which once became a
    'service' called 'Please'.)"""
    types = {"ClusterIP", "NodePort", "LoadBalancer", "ExternalName"}
    names = []
    for ln in kubectl_get_svc.splitlines():
        cols = ln.split()
        if len(cols) >= 2 and cols[1] in types:
            names.append(cols[0])
    return names


def parse_workloads(kubectl_get_workloads: str) -> set[str]:
    """Names from `kubectl get deploy,statefulset -n <ns> --no-headers`
    ('deployment.apps/geo   1/1 ...' -> 'geo')."""
    names = set()
    for ln in kubectl_get_workloads.splitlines():
        cols = ln.split()
        if cols and "/" in cols[0] and cols[0].split("/", 1)[0].startswith(("deployment", "statefulset")):
            names.add(cols[0].split("/", 1)[1])
    return names


def real_services(services: list[str], pods_output: str, workloads: set[str]) -> list[str]:
    """Services that are a workload of their own: some pod is owned by a workload of
    that name, or a Deployment/StatefulSet of that name exists (so a service scaled
    to zero is kept). Drops alias Services such as social-network's 'jaeger-out',
    which selects another workload's pods: every check on it reads 'broken' (no pods,
    no deployment) and fed false fault evidence in the full RQ1 run (catalog v1)."""
    owners = {pod_owner(ln.split()[0]) for ln in pods_output.splitlines() if ln.split()}
    return [s for s in services if s in owners or s in workloads]


# Replies meaning the TOOL could not address the service, not that the service is
# broken. AIOpsLab's get_logs answers 'Error: Your service/namespace does not exist'
# for astronomy-shop's flagd although its pod runs; catalog v1 read that as a crash.
_TOOL_ERRORS = ("Error: Your service/namespace does not exist", "Error from server (NotFound)")


def tool_error(output: str) -> bool:
    return any(m in output[:300] for m in _TOOL_ERRORS)

