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
    python -m testbed_ops.chaos slots               # fixed, seeded schedule for scored sessions
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
    # added 15 Oct, never seen by the agent before scored sessions (held-out):
    "worker_hung": ("worker", "restart:worker"),         # process frozen: no errors, no change, no output
    "collector_hung": ("collector", "restart:collector"),
    "api_down": ("api", "restart:api"),                  # container stopped
    "upstream_cut": ("collector", "none"),               # the internet path to Wikimedia is gone:
}                                                        # not our bug, nothing on the allowlist helps
HUNG = {"worker_hung": "worker", "collector_hung": "collector"}
EGRESS = "egress"   # relay container standing in for the internet path (see docker-compose.yml)


def _faults() -> list[str]:
    """Faults in play this session (CHAOS_FAULTS, comma-separated; default: all)."""
    want = [f.strip() for f in os.environ.get("CHAOS_FAULTS", "").split(",") if f.strip()]
    return [f for f in FAULTS if not want or f in want]

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
    elif fault in HUNG:
        env.container(HUNG[fault]).kill(signal="SIGSTOP")   # the process freezes; Docker still says running
    elif fault == "api_down":
        env.container("api").stop(timeout=5)
    elif fault == "upstream_cut":
        env.container(EGRESS).stop(timeout=2)
    record = {"fault_id": fid, "fault": fault, "service": service, "correct_remedy": remedy}
    env.write_atomic(STATE, json.dumps({**record, "started": env.now()}))
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
        if fault in HUNG:
            c = env.container(HUNG[fault])
            c.reload()
            if c.attrs["State"]["Status"] != "running":
                return False
            st = c.exec_run(["sh", "-c", "grep '^State:' /proc/1/status"]).output.decode()
            return "stopped" not in st and "(T)" not in st and " T " not in st
        if fault == "api_down":
            c = env.container("api")
            c.reload()
            return c.attrs["State"]["Status"] == "running"
        if fault == "upstream_cut":
            c = env.container(EGRESS)
            c.reload()
            return c.attrs["State"]["Status"] == "running"
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
        elif fault in HUNG:
            env.container(HUNG[fault]).kill(signal="SIGCONT")
        elif fault == "api_down":
            env.container("api").start()
        elif fault == "upstream_cut":
            env.container(EGRESS).start()
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
            inject(rng.choice(_faults()))


def slot_plan(seed: int, n: int, faults: list[str], slot_min: float, jitter_min: float) -> list[tuple[float, str]]:
    """(minutes from session start, fault) for n slots. Balanced: every fault appears once per
    round, in a seeded random order, so two sessions with the same seed get the same faults at
    the same times whatever the agents do (scored comparisons are paired fault by fault)."""
    rng = random.Random(seed)
    order: list[str] = []
    while len(order) < n:
        r = list(faults)
        rng.shuffle(r)
        order += r
    return [(i * slot_min + rng.uniform(0, jitter_min), f) for i, f in enumerate(order[:n])]


def slots():
    """Scored sessions: a fixed schedule from CHAOS_SEED. Each slot is longer than a fault can
    last (FAULT_TIMEOUT_MIN) plus time to settle, so faults never overlap."""
    env.ensure_baseline_releases()
    watcher = threading.Thread(target=watch_resolution, daemon=True)
    watcher.start()
    seed = int(os.environ.get("CHAOS_SEED", "0")) or 1
    slot = float(os.environ.get("CHAOS_SLOT_MIN", str(TIMEOUT_MIN + 10)))
    plan = slot_plan(seed, int(os.environ.get("CHAOS_SLOTS", "100")), _faults(), slot,
                     float(os.environ.get("CHAOS_JITTER_MIN", "3")))
    # SESSION_START (epoch seconds) is shared with the traffic replay, so faults and traffic stay
    # aligned however long the build took. If the injector restarts mid-session, slots already
    # past are not injected again.
    start = os.environ.get("SESSION_START")
    t0 = float(start) if start else time.time()
    restarted = start is not None and time.time() > t0 + 60
    _seal({"event": "schedule", "seed": seed, "slot_min": slot, "faults": _faults(),
           "timeout_min": TIMEOUT_MIN, "session_start": t0, "restarted": restarted})
    for at, fault in plan:
        if restarted and t0 + at * 60 < time.time():
            _seal({"event": "slot_skipped_restart", "fault": fault})
            continue
        time.sleep(max(0.0, t0 + at * 60 - time.time()))
        while _active():            # never happens when slot > timeout; recorded if it does
            _seal({"event": "slot_delayed", "fault": fault})
            time.sleep(30)
        try:
            inject(fault)
        except Exception as e:      # a fault that cannot be injected is recorded, not silently skipped
            _seal({"event": "inject_failed", "fault": fault, "error": str(e)[:300]})
    # stay up after the last slot: the watcher must still see the last fault resolve or time out,
    # and exiting would make Docker restart the injector (a "restarted" schedule every minute)
    watcher.join()


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
    elif cmd == "slots":
        slots()
    elif cmd == "serve":           # idle with the resolution watcher (manual mode)
        env.ensure_baseline_releases()
        watch_resolution()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
