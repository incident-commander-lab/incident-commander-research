"""Fault injector with a sealed ground-truth log.

Each fault is something that really happens in production, done for real to the
running system. The answer key (which fault, where, when, how it ended) goes to
/sealed/ground_truth.jsonl, a volume the agent does not mount. Deploys done by the
injector appear in the shared change log as an ordinary pipeline deploy, so the
agent sees what an on-call engineer would see, nothing more.

    python -m testbed_ops.chaos list
    python -m testbed_ops.chaos inject <fault>      # one fault now
    python -m testbed_ops.chaos heal                # end the active fault
    python -m testbed_ops.chaos schedule            # random faults forever (CHAOS_MEAN_GAP_MIN)
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import env

SEALED = Path(os.environ.get("SEALED_DIR", "/sealed"))
STATE = Path(os.environ.get("CHAOS_STATE", "/sealed/active.json"))
MEAN_GAP_MIN = float(os.environ.get("CHAOS_MEAN_GAP_MIN", "30"))
TIMEOUT_MIN = float(os.environ.get("FAULT_TIMEOUT_MIN", "20"))
PIPELINE = "deploy-pipeline"   # what the change log shows for injected deploys

# fault -> (service at fault, what a correct responder does)
FAULTS = {
    "bad_release_worker": ("worker", "rollback_release:worker"),
    "crash_loop_worker": ("worker", "rollback_release:worker"),
    "bad_config_collector": ("collector", "rollback_release:collector"),
    "db_lock": ("postgres", "kill_blocking_sessions:postgres"),
    "redis_oom": ("redis", "restore_redis_maxmemory:redis"),
}

_lock_conn = None   # db_lock holds its lock on this connection


def _seal(event: dict):
    SEALED.mkdir(parents=True, exist_ok=True)
    with (SEALED / "ground_truth.jsonl").open("a") as f:
        f.write(json.dumps({"ts": env.now(), **event}) + "\n")


def _active():
    return json.loads(STATE.read_text()) if STATE.exists() else None


def _bump(version: str) -> str:
    major, minor, *_ = (version.split(".") + ["0", "0"])[:3]
    return f"{major}.{int(minor) + 1}.0" if minor.isdigit() else "1.1.0"


def inject(fault: str) -> dict:
    if _active():
        raise RuntimeError(f"a fault is already active: {_active()}")
    service, remedy = FAULTS[fault]
    fid = uuid.uuid4().hex[:8]
    if fault in ("bad_release_worker", "crash_loop_worker", "bad_config_collector"):
        cur = env.current_release(service)
        bad_env = {"bad_release_worker": {"DELTA_COLUMN": "deltas"},
                   "crash_loop_worker": {"BATCH_SIZE": "0"},
                   "bad_config_collector": {"WIKIMEDIA_URL": "https://stream.wikimedia.org/v2/stream/recentchanges"},
                   }[fault]
        env.deploy(service, {"version": _bump(cur.get("version", "1.0.0")), "env": {**cur.get("env", {}), **bad_env}},
                   actor=PIPELINE, reason="routine release")
    elif fault == "db_lock":
        _start_db_lock(fid)
    elif fault == "redis_oom":
        env.rds().config_set("maxmemory", "1mb")
    record = {"fault_id": fid, "fault": fault, "service": service, "correct_remedy": remedy}
    STATE.write_text(json.dumps({**record, "started": env.now()}))
    _seal({"event": "inject", **record})
    return record


def _start_db_lock(fid: str):
    """An 'ad-hoc maintenance query' that takes an exclusive table lock and never commits."""
    global _lock_conn
    _lock_conn = env.psycopg.connect(env.PG_DSN, autocommit=False)
    cur = _lock_conn.cursor()
    cur.execute("SET application_name = 'maintenance-reindex'")
    cur.execute("LOCK TABLE edits IN ACCESS EXCLUSIVE MODE")


def is_resolved(fault: str) -> bool:
    """Has the system recovered from this fault (by anyone's hand)?"""
    try:
        if fault in ("bad_release_worker", "crash_loop_worker"):
            r = env.current_release("worker")["env"]
            return r.get("DELTA_COLUMN", "delta") == "delta" and r.get("BATCH_SIZE", "200") != "0"
        if fault == "bad_config_collector":
            return "recentchanges" not in env.current_release("collector")["env"].get("WIKIMEDIA_URL", "")
        if fault == "db_lock":
            with env.pg() as c:
                n = c.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='maintenance-reindex'").fetchone()[0]
            return n == 0
        if fault == "redis_oom":
            return env.rds().config_get("maxmemory")["maxmemory"] != str(1024 * 1024)
    except Exception:
        return False
    return False


def heal(reason: str = "manual") -> dict | None:
    """End the active fault the way an operator would, if nobody has fixed it yet."""
    global _lock_conn
    a = _active()
    if not a:
        return None
    fault = a["fault"]
    already = is_resolved(fault)
    if not already:
        if fault in ("bad_release_worker", "crash_loop_worker", "bad_config_collector"):
            env.rollback(a["service"], actor=PIPELINE, reason="release reverted")
        elif fault == "db_lock":
            with env.pg() as c:
                c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='maintenance-reindex'")
        elif fault == "redis_oom":
            env.rds().config_set("maxmemory", env.REDIS_BASELINE_MAXMEMORY)
    if _lock_conn is not None:
        try:
            _lock_conn.close()
        except Exception:
            pass
        _lock_conn = None
    _seal({"event": "end", "fault_id": a["fault_id"], "fault": fault,
           "ended_by": "fixed_before_injector_heal" if already else reason})
    STATE.unlink(missing_ok=True)
    return a


def watch_resolution(poll: int = 15):
    """Seal the moment the active fault stops (whoever fixed it), or heal it at the timeout."""
    while True:
        a = _active()
        if a:
            started = datetime.fromisoformat(a["started"]).timestamp()
            if is_resolved(a["fault"]):
                _seal({"event": "resolved", "fault_id": a["fault_id"], "fault": a["fault"],
                       "minutes": round((time.time() - started) / 60, 1)})
                heal(reason="resolved")
            elif time.time() - started > TIMEOUT_MIN * 60:
                heal(reason="timeout")
        time.sleep(poll)


def schedule():
    """Random, unannounced faults: exponential gaps, one at a time."""
    env.ensure_baseline_releases()
    threading.Thread(target=watch_resolution, daemon=True).start()
    rng = random.Random(int(os.environ.get("CHAOS_SEED", "0")) or None)
    while True:
        gap = rng.expovariate(1 / (MEAN_GAP_MIN * 60))
        time.sleep(gap)
        if not _active():
            inject(rng.choice(list(FAULTS)))


def main(argv: list[str]):
    env.ensure_baseline_releases()
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        for f, (svc, remedy) in FAULTS.items():
            print(f"{f:22} service={svc:10} correct response: {remedy}")
    elif cmd == "inject":
        print(json.dumps(inject(argv[1])))
        if argv[1] == "db_lock":     # the lock lives as long as this connection: hold it
            while _lock_conn is not None and not _lock_conn.closed:
                try:
                    _lock_conn.execute("SELECT 1")   # fails once the session is terminated
                except Exception:
                    break
                time.sleep(5)
    elif cmd == "heal":
        print(json.dumps(heal()))
    elif cmd == "schedule":
        schedule()
    elif cmd == "serve":           # idle with the resolution watcher (manual mode)
        env.ensure_baseline_releases()
        watch_resolution()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
