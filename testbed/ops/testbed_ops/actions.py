"""The only actions the agent can take. Each one is reversible or data-safe, returns an
undo (or None when there is nothing to undo), and is checked by OPA before it runs."""
from __future__ import annotations

from typing import Callable, Optional

from . import env

ACTOR = "incident-commander"

# remedy -> (reversible, description)
CATALOG = {
    "rollback_release:worker": (True, "Roll the worker back to its previous release"),
    "rollback_release:collector": (True, "Roll the collector back to its previous release"),
    "restart:worker": (True, "Restart the worker container"),
    "restart:collector": (True, "Restart the collector container"),
    "restart:api": (True, "Restart the API container"),
    "kill_blocking_sessions:postgres": (True, "Terminate Postgres sessions that block others (their open transaction rolls back; no data is lost)"),
    "restore_redis_maxmemory:redis": (True, "Set Redis maxmemory back to its baseline"),
    "none": (True, "No action"),
}


def execute(remedy: str, reason: str) -> Optional[Callable[[], None]]:
    """Run a remedy. Returns a function that undoes it, or None."""
    kind, _, svc = remedy.partition(":")
    if kind == "rollback_release":
        bad = env.rollback(svc, actor=ACTOR, reason=reason)
        if bad is None:
            raise RuntimeError(f"{svc} has no previous release to roll back to")
        return lambda: env.deploy(svc, bad, actor=ACTOR, reason="auto-revert: rollback did not restore service")
    if kind == "restart":
        env.container(svc).restart(timeout=10)
        env.record_change(svc, "restart", ACTOR, reason)
        return None
    if kind == "kill_blocking_sessions":
        with env.pg() as c:
            killed = c.execute("""
                SELECT pid, application_name, pg_terminate_backend(pid) FROM pg_stat_activity
                 WHERE pid IN (SELECT unnest(pg_blocking_pids(pid)) FROM pg_stat_activity
                                WHERE wait_event_type = 'Lock')""").fetchall()
        env.record_change("postgres", "terminate_sessions", ACTOR, reason,
                          sessions=[{"pid": k[0], "application": k[1]} for k in killed])
        return None
    if kind == "restore_redis_maxmemory":
        r = env.rds()
        before = r.config_get("maxmemory")["maxmemory"]
        r.config_set("maxmemory", env.REDIS_BASELINE_MAXMEMORY)
        env.record_change("redis", "config", ACTOR, reason, setting="maxmemory",
                          frm=before, to=env.REDIS_BASELINE_MAXMEMORY)
        return lambda: r.config_set("maxmemory", before)
    if kind == "none":
        return None
    raise ValueError(f"unknown remedy {remedy}")
