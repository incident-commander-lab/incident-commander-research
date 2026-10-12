"""The agent's read-only checks on the test bed, in the same form as the benchmark's
probe catalog (ic/bench/probes.py), so the frozen decision rule (ic/bench/decide.py)
runs on them unchanged. The last outcome of every template is the healthy one."""
from __future__ import annotations

import json
import re

from ic.bench.probes import ProbeInstance, ProbeTemplate

from . import env

_LOG_CATS = [
    ("db_errors", re.compile(r"postgres|column .* does not exist|relation|deadlock|lock|query failed|store failed", re.I)),
    ("redis_errors", re.compile(r"redis|OOM|maxmemory|enqueue failed", re.I)),
    ("source_errors", re.compile(r"source disconnected|HTTP [45]\d\d|stream ended", re.I)),
    ("crash", re.compile(r"invalid config|uncaught|fatal|exited", re.I)),
]


def _label_logs(text: str, _svc: str) -> str:
    """Most frequent error category among warn/error lines, or 'clean'."""
    counts = {c: 0 for c, _ in _LOG_CATS}
    for line in text.splitlines():
        try:
            rec = json.loads(line[line.index("{"):])
        except ValueError:
            continue
        if rec.get("level") not in ("warn", "error"):
            continue
        blob = f"{rec.get('msg', '')} {rec.get('error', '')}"
        for cat, rx in _LOG_CATS:
            if rx.search(blob):
                counts[cat] += 1
                break
    best = max(counts, key=counts.get)
    return best if counts[best] >= 2 else "clean"


def _read_container(svc: str) -> str:
    c = env.container(svc)
    c.reload()
    st = c.attrs["State"]
    return json.dumps({"status": st.get("Status"), "restarting": st.get("Restarting"),
                       "restart_count": c.attrs.get("RestartCount", 0), "started": st.get("StartedAt"),
                       "exit_code": st.get("ExitCode")})


def _label_container(out: str, _svc: str) -> str:
    s = json.loads(out)
    if s["restarting"] or (s["status"] == "running" and s["restart_count"] >= 2 and s["exit_code"] not in (0, None)):
        return "crash_looping"
    return "running" if s["status"] == "running" else "stopped"


_THROUGHPUT = {"collector": "sum(rate(pulse_events_enqueued_total[45s]))",
               "worker": "sum(rate(pulse_events_processed_total[45s]))"}


def _read_throughput(svc: str) -> str:
    return json.dumps({"rate_per_s": env.prom(_THROUGHPUT[svc])})


def _label_throughput(out: str, _svc: str) -> str:
    r = json.loads(out)["rate_per_s"]
    return "flowing" if r and r > 0.5 else "stalled"


def _read_changes(svc: str) -> str:
    return json.dumps([c for c in env.recent_changes(30) if c["service"] == svc])


def _label_changes(out: str, _svc: str) -> str:
    return "changed" if json.loads(out) else "unchanged"


def _read_source(_svc: str) -> str:
    return json.dumps({"connected": env.prom("max(pulse_source_connected)"),
                       "reconnects_10m": env.prom("sum(increase(pulse_source_reconnects_total[10m]))")})


def _label_source(out: str, _svc: str) -> str:
    return "connected" if (json.loads(out)["connected"] or 0) >= 1 else "disconnected"


def _read_redis_mem(_svc: str) -> str:
    info = env.rds().info("memory")
    return json.dumps({"used": info.get("used_memory"), "max": info.get("maxmemory")})


def _label_redis_mem(out: str, _svc: str) -> str:
    m = json.loads(out)
    return "full" if m["max"] and m["used"] / m["max"] > 0.9 else "ok"


def _read_db_locks(_svc: str) -> str:
    with env.pg() as c:
        rows = c.execute("""SELECT count(*) FROM pg_stat_activity
                            WHERE wait_event_type = 'Lock' AND datname = current_database()""").fetchone()
    return json.dumps({"sessions_waiting_on_locks": rows[0]})


def _label_db_locks(out: str, _svc: str) -> str:
    return "blocked" if json.loads(out)["sessions_waiting_on_locks"] > 0 else "clear"


def _read_lag(_svc: str) -> str:
    return json.dumps({"lag": env.prom("max(pulse_stream_lag)"), "pending": env.prom("max(pulse_stream_pending)")})


def _label_lag(out: str, _svc: str) -> str:
    return "growing" if (json.loads(out)["lag"] or 0) > 1000 else "ok"


def _t(name, outcomes, cost, describe, read, label, eps):
    # `call` is unused here (the benchmark sends it to AIOpsLab); readers run locally.
    return ProbeTemplate(name, outcomes, cost, describe, call=lambda ns, s, n=name: f"{n}:{s}", label=label,
                         eps=eps), read


_CATALOG = {
    "container_state": _t("container_state", ("crash_looping", "stopped", "running"), 1.0,
                          "Container status and restarts", _read_container, _label_container, 0.05),
    "error_logs": _t("error_logs", ("db_errors", "redis_errors", "source_errors", "crash", "clean"), 2.0,
                     "Dominant category of warn/error log lines in the last 2 minutes",
                     lambda s: env.logs(s, 120), _label_logs, 0.20),
    "throughput": _t("throughput", ("stalled", "flowing"), 1.0,
                     "Events per second this stage passed on in the last 45 s (Prometheus)", _read_throughput, _label_throughput, 0.05),
    "recent_change": _t("recent_change", ("changed", "unchanged"), 1.0,
                        "Any deploy or rollback of this service in the last 30 minutes (change log)",
                        _read_changes, _label_changes, 0.02),
    "source_connected": _t("source_connected", ("disconnected", "connected"), 1.0,
                           "Is the collector connected to the Wikimedia stream", _read_source, _label_source, 0.05),
    "redis_memory": _t("redis_memory", ("full", "ok"), 1.0, "Redis used memory vs maxmemory",
                       _read_redis_mem, _label_redis_mem, 0.05),
    "db_locks": _t("db_locks", ("blocked", "clear"), 1.0, "Postgres sessions waiting on locks",
                   _read_db_locks, _label_db_locks, 0.05),
    "stream_lag": _t("stream_lag", ("growing", "ok"), 1.0, "Events waiting in the Redis stream for the worker",
                     _read_lag, _label_lag, 0.05),
}

# which checks exist for which service
APPLIES = {
    "collector": ["container_state", "error_logs", "throughput", "recent_change", "source_connected"],
    "worker": ["container_state", "error_logs", "throughput", "recent_change", "stream_lag"],
    "api": ["container_state", "error_logs"],
    "redis": ["container_state", "redis_memory"],
    "postgres": ["container_state", "db_locks"],
}

TEMPLATES = {n: t for n, (t, _) in _CATALOG.items()}


def instances() -> dict[str, ProbeInstance]:
    return {f"{n}:{s}": ProbeInstance(TEMPLATES[n], s) for s, names in APPLIES.items() for n in names}


def run(probe: ProbeInstance) -> tuple[str, str]:
    """Execute a check: (raw output, label). A failed read is reported as raw text with label None."""
    read = _CATALOG[probe.template.name][1]
    try:
        raw = read(probe.service)
    except Exception as e:  # tool failure is not evidence (catalog v2 rule)
        return f"ERROR: {e}", None
    return raw, probe.label(raw)
