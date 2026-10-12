"""Access to the running test bed: containers (Docker API), Prometheus, Postgres,
Redis, releases and the change log. Shared by the fault injector and the agent."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Optional

import docker
import httpx
import psycopg
import redis as redis_lib

PROJECT = os.environ.get("COMPOSE_PROJECT", "pulse")
PROM = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
PG_DSN = os.environ.get("PG_DSN", "postgresql://pulse:pulse@postgres:5432/pulse")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
RELEASES = Path(os.environ.get("RELEASES_DIR", "/releases"))
CHANGES = Path(os.environ.get("CHANGES_LOG", "/state/changes.jsonl"))
REDIS_BASELINE_MAXMEMORY = os.environ.get("REDIS_BASELINE_MAXMEMORY", "256mb")

SERVICES = ["collector", "worker", "api", "redis", "postgres"]
RELEASED = ["collector", "worker"]           # services deployed through release files

_docker = None


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def dk():
    global _docker
    if _docker is None:
        _docker = docker.from_env()
    return _docker


def container(service: str):
    found = dk().containers.list(all=True, filters={
        "label": [f"com.docker.compose.project={PROJECT}", f"com.docker.compose.service={service}"]})
    if not found:
        raise LookupError(f"no container for service {service}")
    return found[0]


def logs(service: str, seconds: int = 120) -> str:
    since = int(time.time()) - seconds
    return container(service).logs(since=since, tail=400).decode("utf-8", "replace")


def prom(query: str) -> Optional[float]:
    """Scalar result of a PromQL query (sum of the vector), or None when there is no data."""
    r = httpx.get(f"{PROM}/api/v1/query", params={"query": query}, timeout=10)
    r.raise_for_status()
    res = r.json()["data"]["result"]
    if not res:
        return None
    return sum(float(x["value"][1]) for x in res)


def pg():
    return psycopg.connect(PG_DSN, autocommit=True, connect_timeout=5)


def rds():
    return redis_lib.Redis.from_url(REDIS_URL, socket_timeout=5)


# ------------------------------------------------------------------ releases
def current_release(service: str) -> dict:
    p = RELEASES / f"{service}.json"
    return json.loads(p.read_text()) if p.exists() else {"version": "1.0.0", "env": {}}


def _history(service: str) -> list[dict]:
    p = RELEASES / f"{service}.history.json"
    return json.loads(p.read_text()) if p.exists() else []


def deploy(service: str, release: dict, actor: str, reason: str) -> dict:
    """Write the release file, record the change, restart the container."""
    old = current_release(service)
    hist = _history(service) + [old]
    (RELEASES / f"{service}.history.json").write_text(json.dumps(hist[-20:], indent=1))
    (RELEASES / f"{service}.json").write_text(json.dumps(release, indent=1))
    record_change(service, "deploy", actor, reason, frm=old.get("version"), to=release.get("version"))
    container(service).restart(timeout=10)
    return old


def rollback(service: str, actor: str, reason: str) -> Optional[dict]:
    """Back to the previous release. Returns the release that was rolled back (for a revert)."""
    hist = _history(service)
    if not hist:
        return None
    prev = hist[-1]
    bad = current_release(service)
    (RELEASES / f"{service}.history.json").write_text(json.dumps(hist[:-1], indent=1))
    (RELEASES / f"{service}.json").write_text(json.dumps(prev, indent=1))
    record_change(service, "rollback", actor, reason, frm=bad.get("version"), to=prev.get("version"))
    container(service).restart(timeout=10)
    return bad


def ensure_baseline_releases():
    RELEASES.mkdir(parents=True, exist_ok=True)
    for s in RELEASED:
        p = RELEASES / f"{s}.json"
        if not p.exists():
            p.write_text(json.dumps({"version": "1.0.0", "env": {}}, indent=1))


# ------------------------------------------------------------------ change log
def record_change(service: str, kind: str, actor: str, reason: str, **extra):
    CHANGES.parent.mkdir(parents=True, exist_ok=True)
    with CHANGES.open("a") as f:
        f.write(json.dumps({"ts": now(), "service": service, "kind": kind, "actor": actor,
                            "reason": reason, **extra}) + "\n")


def recent_changes(minutes: int = 30) -> list[dict]:
    if not CHANGES.exists():
        return []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    out = []
    for line in CHANGES.read_text().splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        if dt.datetime.fromisoformat(c["ts"]) >= cutoff:
            out.append(c)
    return out
