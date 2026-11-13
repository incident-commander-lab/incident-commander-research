"""Fault injector for the cluster, with a sealed answer key (same design and event format as
testbed/ops/testbed_ops/chaos.py, so testbed_ops.score scores cluster sessions unchanged).

Faults happen for real, under load. Releases are made the way a pipeline would (a Deployment
rollout annotated "routine release"), so the agent sees what an on-call engineer would see in
`kubectl rollout history`, nothing more. The answer key goes to /sealed (a volume in the `chaos`
namespace; the agent's RBAC has no access to that namespace).

    python chaos.py list | inject <fault> | heal | serve | slots
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from kubernetes import client, config
from kubernetes.stream import stream

SEALED = Path(os.environ.get("SEALED_DIR", "/sealed"))
STATE = SEALED / "active.json"
TIMEOUT_MIN = float(os.environ.get("FAULT_TIMEOUT_MIN", "15"))
SURGE_USERS = int(os.environ.get("SURGE_USERS", "150"))          # AWS: 2.5 x the trace peak
GPU_NUM_CTX = os.environ.get("CHAOS_GPU_NUM_CTX", "16384")        # capped locally; AWS: large enough to overflow the L4
REPLAY_IMAGE = os.environ.get("REPLAY_IMAGE", "ic/replay:1.0.0")
PIPELINE = "deploy-pipeline"

# fault -> (service at fault, correct response, also accepted)
FAULTS = {
    "cpu_saturation_shop": ("juice-shop", "scale_out:juice-shop", []),
    "memory_leak_assistant": ("assistant", "rollout_undo:assistant", []),
    "gpu_memory_exhausted": ("assistant", "rollout_undo:assistant", ["rate_limit:assistant"]),
    # shakedown only (SEEN): a release pointing the assistant at a wrong model address -> errors
    "assistant_bad_url": ("assistant", "rollout_undo:assistant", []),
    # security (week 2): one client replays attack payloads -> escalate and propose block_client (needs a human)
    "attack_burst": ("juice-shop", "block_client", []),
    # Pulse (testbed/) in the cluster, week 2. The five original faults were seen in the Compose
    # shakedowns; the four added 15 Oct are held out (testbed_ops/chaos.py). Fixes in cluster terms.
    "bad_release_worker": ("worker", "rollout_undo:worker", []),
    "crash_loop_worker": ("worker", "rollout_undo:worker", []),
    "bad_config_collector": ("collector", "rollout_undo:collector", []),
    "db_lock": ("postgres", "kill_blocking_sessions:postgres", []),
    "redis_oom": ("redis", "restore_redis_maxmemory:redis", []),
    "worker_hung": ("worker", "restart:worker", []),
    "collector_hung": ("collector", "restart:collector", []),
    "api_down": ("api", "scale_out:api", []),          # Compose: container stopped; here: scaled to 0
    "upstream_cut": ("collector", "none", []),         # the internet path is gone: nothing of ours to fix
}
NS = {"juice-shop": "shop", "assistant": "shop", "collector": "pulse", "worker": "pulse", "api": "pulse",
      "redis": "pulse", "postgres": "pulse", "egress": "pulse"}
PULSE_BAD = {"bad_release_worker": {"DELTA_COLUMN": "deltas"}, "crash_loop_worker": {"BATCH_SIZE": "0"},
             "bad_config_collector": {"WIKIMEDIA_URL": "https://stream.wikimedia.org/v2/stream/recentchanges"}}
HUNG = {"worker_hung": "worker", "collector_hung": "collector"}
PG_DSN = os.environ.get("PULSE_PG", "postgresql://pulse:pulse@postgres.pulse.svc:5432/pulse")
REDIS = os.environ.get("PULSE_REDIS", "redis://redis.pulse.svc:6379")
REDIS_BASELINE = 256 * 1024 * 1024
SEEN = {"assistant_bad_url"}   # used to shake the agent down; never scored as held-out
BASE = {"NUM_CTX": "2048", "MAX_CONCURRENCY": "4", "RETAIN_ANSWERS": "false"}

if os.environ.get("KUBERNETES_SERVICE_HOST"):
    config.load_incluster_config()
else:
    config.load_kube_config()
apps, batch, crd, core = client.AppsV1Api(), client.BatchV1Api(), client.CustomObjectsApi(), client.CoreV1Api()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seal(event: dict):
    SEALED.mkdir(parents=True, exist_ok=True)
    with (SEALED / "ground_truth.jsonl").open("a") as f:
        f.write(json.dumps({"ts": now(), **event}) + "\n")


def _active():
    return json.loads(STATE.read_text()) if STATE.exists() else None


def _env(deploy: str) -> dict:
    d = apps.read_namespaced_deployment(deploy, NS[deploy])
    return {e.name: e.value for e in d.spec.template.spec.containers[0].env or []}


def _release(deploy: str, changes: dict, reason: str):
    """A rollout, as the pipeline would do it: new env values, version bump, change-cause."""
    env = _env(deploy)
    vkey = "ASSISTANT_VERSION" if deploy == "assistant" else "RELEASE_VERSION"
    major, minor, *_ = (env.get(vkey, "1.0.0").split(".") + ["0", "0"])[:3]
    changes = {vkey: f"{major}.{int(minor) + 1}.0", **changes}
    patch = {"metadata": {"annotations": {"kubernetes.io/change-cause": f"{PIPELINE}: {reason}"}},
             "spec": {"template": {"spec": {"containers": [
                 {"name": deploy, "env": [{"name": k, "value": v} for k, v in changes.items()]}]}}}}
    apps.patch_namespaced_deployment(deploy, NS[deploy], patch)


def _scale(deploy: str, n: int):
    apps.patch_namespaced_deployment_scale(deploy, NS[deploy], {"spec": {"replicas": n}})


def _replicas(deploy: str) -> int:
    return apps.read_namespaced_deployment(deploy, NS[deploy]).spec.replicas or 0


def _pod(deploy: str) -> str:
    pods = core.list_namespaced_pod(NS[deploy], label_selector=f"app={deploy}").items
    running = [p for p in pods if p.status.phase == "Running" and not p.metadata.deletion_timestamp]
    return running[0].metadata.name if running else ""


def _freeze(deploy: str) -> str:
    """Stop the app process in its pod (SIGSTOP): the pod still looks Running, nothing gets done."""
    pod = _pod(deploy)
    stream(core.connect_get_namespaced_pod_exec, pod, NS[deploy], container=deploy,
           command=["sh", "-c", f"pkill -STOP -f 'node src/{deploy}.js'"], stderr=True, stdout=True, stdin=False, tty=False)
    return pod


def _db_lock_job():
    """An ad-hoc 'maintenance' job that takes an exclusive table lock and never commits."""
    sql = "SET application_name='maintenance-reindex'; BEGIN; LOCK TABLE edits IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(86400);"
    job = client.V1Job(
        metadata=client.V1ObjectMeta(name="maintenance-reindex", namespace="pulse"),
        spec=client.V1JobSpec(backoff_limit=0, ttl_seconds_after_finished=600, template=client.V1PodTemplateSpec(
            spec=client.V1PodSpec(restart_policy="Never", node_selector={"role": "ops"}, containers=[client.V1Container(
                name="psql", image="postgres:16-alpine", command=["psql", PG_DSN.replace("postgres.pulse.svc", "postgres"),
                                                                  "-v", "ON_ERROR_STOP=1", "-c", sql],
                resources=client.V1ResourceRequirements(requests={"cpu": "10m", "memory": "16Mi"}, limits={"memory": "64Mi"}))]))))
    batch.create_namespaced_job("pulse", job)


def _pg_lock_sessions() -> int:
    import psycopg
    with psycopg.connect(PG_DSN, autocommit=True) as c:
        return c.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='maintenance-reindex'").fetchone()[0]


def _surge(minutes: float):
    """Extra shoppers for the fault's lifetime: a Locust job in constant mode."""
    job = client.V1Job(
        metadata=client.V1ObjectMeta(name="locust-surge", namespace="load"),
        spec=client.V1JobSpec(backoff_limit=0, ttl_seconds_after_finished=600, template=client.V1PodTemplateSpec(
            spec=client.V1PodSpec(restart_policy="Never", node_selector={"role": "ops"}, containers=[client.V1Container(
                name="locust", image="locustio/locust:2.33.0",
                args=["-f", "/mnt/locustfile.py", "--headless", "-u", str(SURGE_USERS), "-r", "10",
                      "-t", f"{int(minutes)}m", "--host", "http://traefik.kube-system.svc.cluster.local", "--only-summary"],
                volume_mounts=[client.V1VolumeMount(name="lf", mount_path="/mnt")],
                resources=client.V1ResourceRequirements(requests={"cpu": "200m", "memory": "128Mi"}, limits={"memory": "512Mi"}))],
                volumes=[client.V1Volume(name="lf", config_map=client.V1ConfigMapVolumeSource(name="locustfile"))]))))
    batch.create_namespaced_job("load", job)


def _attack(minutes: float):
    """One client sending attack payloads at the shop for the fault's lifetime (cluster/load/replay)."""
    job = client.V1Job(
        metadata=client.V1ObjectMeta(name="attack", namespace="load"),
        spec=client.V1JobSpec(backoff_limit=0, ttl_seconds_after_finished=600, template=client.V1PodTemplateSpec(
            spec=client.V1PodSpec(restart_policy="Never", node_selector={"role": "ops"}, containers=[client.V1Container(
                name="replay", image=REPLAY_IMAGE,
                env=[client.V1EnvVar(name="MODE", value="attack"), client.V1EnvVar(name="ATTACK_SECONDS", value=str(int(minutes * 60)))],
                volume_mounts=[client.V1VolumeMount(name="attacks", mount_path="/attacks")],
                resources=client.V1ResourceRequirements(requests={"cpu": "50m", "memory": "64Mi"}, limits={"memory": "256Mi"}))],
                volumes=[client.V1Volume(name="attacks", config_map=client.V1ConfigMapVolumeSource(name="attack-payloads"))]))))
    batch.create_namespaced_job("load", job)


def _blocks() -> int:
    """Client blocks in place at the ingress (the agent's block_client, after a human approved it)."""
    r = crd.list_namespaced_custom_object("traefik.io", "v1alpha1", "shop", "ingressroutes",
                                          label_selector="incident-commander=block")
    return len(r.get("items", []))


def inject(fault: str) -> dict:
    if _active():
        raise RuntimeError(f"a fault is already active: {_active()}")
    service, remedy, also = FAULTS[fault]
    fid = uuid.uuid4().hex[:8]
    restore: dict = {}
    if fault == "cpu_saturation_shop":
        _surge(TIMEOUT_MIN + 1)
    elif fault == "attack_burst":
        restore = {"blocks_before": _blocks()}
        _attack(TIMEOUT_MIN + 1)
    elif fault == "memory_leak_assistant":
        _release("assistant", {"RETAIN_ANSWERS": "true"}, "routine release (answer cache)")
    elif fault == "gpu_memory_exhausted":
        _release("assistant", {"NUM_CTX": GPU_NUM_CTX}, "routine release (longer answers)")
    elif fault == "assistant_bad_url":
        restore = {"OLLAMA_URL": _env("assistant")["OLLAMA_URL"]}
        _release("assistant", {"OLLAMA_URL": "http://model-gateway.ai.svc:11434"}, "routine release (model gateway)")
    elif fault in PULSE_BAD:
        restore = {k: _env(service).get(k, "") for k in PULSE_BAD[fault]}
        _release(service, PULSE_BAD[fault], "routine release")
    elif fault == "db_lock":
        _db_lock_job()
    elif fault == "redis_oom":
        import redis
        redis.from_url(REDIS).config_set("maxmemory", "1mb")
    elif fault in HUNG:
        restore = {"frozen_pod": _freeze(service)}
    elif fault == "api_down":
        restore = {"replicas": _replicas("api")}
        _scale("api", 0)
    elif fault == "upstream_cut":
        restore = {"replicas": _replicas("egress")}
        _scale("egress", 0)
    record = {"fault_id": fid, "fault": fault, "service": service, "correct_remedy": remedy, "also_correct": also}
    STATE.write_text(json.dumps({**record, "started": now(), **({"restore": restore} if restore else {})}))
    _seal({"event": "inject", **record})
    return record


def is_resolved(fault: str) -> bool:
    try:
        if fault == "cpu_saturation_shop":
            return (apps.read_namespaced_deployment("juice-shop", "shop").spec.replicas or 1) >= 2
        if fault == "attack_burst":   # a new block since the burst began (who is blocked is in the incident file)
            return _blocks() > (_active() or {}).get("restore", {}).get("blocks_before", 0)
        if fault in ("bad_release_worker", "crash_loop_worker"):
            e = _env("worker")
            return e.get("DELTA_COLUMN", "delta") == "delta" and e.get("BATCH_SIZE", "200") != "0"
        if fault == "bad_config_collector":
            return "recentchanges" not in _env("collector").get("WIKIMEDIA_URL", "")
        if fault == "db_lock":
            return _pg_lock_sessions() == 0
        if fault == "redis_oom":
            import redis
            return int(redis.from_url(REDIS).config_get("maxmemory")["maxmemory"]) != 1024 * 1024
        if fault in HUNG:            # restarted: the frozen pod is gone and a fresh one runs
            frozen = (_active() or {}).get("restore", {}).get("frozen_pod")
            return bool(_pod(HUNG[fault])) and _pod(HUNG[fault]) != frozen
        if fault == "api_down":
            return _replicas("api") >= 1
        if fault == "upstream_cut":
            return _replicas("egress") >= 1
        env = _env("assistant")
        if fault == "memory_leak_assistant":
            return env.get("RETAIN_ANSWERS", "false") != "true"
        if fault == "assistant_bad_url":
            return "model-gateway" not in env.get("OLLAMA_URL", "")
        if fault == "gpu_memory_exhausted":
            rate_limited = int(env.get("MAX_CONCURRENCY", BASE["MAX_CONCURRENCY"])) < int(BASE["MAX_CONCURRENCY"])
            return env.get("NUM_CTX") == BASE["NUM_CTX"] or rate_limited
    except Exception:
        return False
    return False


def heal(reason: str = "manual") -> dict | None:
    """End the active fault the way an operator would, if nobody has fixed it yet."""
    a = _active()
    if not a:
        return None
    fault, already = a["fault"], is_resolved(a["fault"])
    if fault in ("cpu_saturation_shop", "attack_burst"):   # the load ends either way; the agent's change stays
        try:
            batch.delete_namespaced_job("locust-surge" if fault == "cpu_saturation_shop" else "attack", "load",
                                        propagation_policy="Background")
        except client.ApiException:
            pass
    elif not already and fault == "assistant_bad_url":
        _release("assistant", a["restore"], "release reverted")
    elif fault in PULSE_BAD:
        if not already:
            _release(FAULTS[fault][0], a["restore"], "release reverted")
    elif fault == "db_lock":
        if not already:
            import psycopg
            with psycopg.connect(PG_DSN, autocommit=True) as c:
                c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='maintenance-reindex'")
        try:
            batch.delete_namespaced_job("maintenance-reindex", "pulse", propagation_policy="Background")
        except client.ApiException:
            pass
    elif fault == "redis_oom":
        if not already:
            import redis
            redis.from_url(REDIS).config_set("maxmemory", str(REDIS_BASELINE))
    elif fault in HUNG:
        if not already:
            apps.patch_namespaced_deployment(HUNG[fault], "pulse", {"spec": {"template": {"metadata": {"annotations": {
                "kubectl.kubernetes.io/restartedAt": now()}}}}})
    elif fault in ("api_down", "upstream_cut"):
        if not already:
            _scale("api" if fault == "api_down" else "egress", max(1, a["restore"]["replicas"]))
    elif not already:
        _release("assistant", dict(BASE), "release reverted")
    _seal({"event": "end", "fault_id": a["fault_id"], "fault": fault,
           "ended_by": "fixed_before_injector_heal" if already else reason})
    STATE.unlink(missing_ok=True)
    return a


def watch_resolution(poll: int = 15):
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


def slot_plan(seed: int, n: int, faults: list[str], slot_min: float, jitter_min: float):
    rng = random.Random(seed)
    order: list[str] = []
    while len(order) < n:
        r = list(faults)
        rng.shuffle(r)
        order += r
    return [(i * slot_min + rng.uniform(0, jitter_min), f) for i, f in enumerate(order[:n])]


def slots():
    threading.Thread(target=watch_resolution, daemon=True).start()
    seed = int(os.environ.get("CHAOS_SEED", "0")) or 1
    slot = float(os.environ.get("CHAOS_SLOT_MIN", str(TIMEOUT_MIN + 10)))
    faults = [f for f in FAULTS if f not in SEEN and f in (os.environ.get("CHAOS_FAULTS") or ",".join(FAULTS)).split(",")]
    _seal({"event": "schedule", "seed": seed, "slot_min": slot, "faults": faults, "timeout_min": TIMEOUT_MIN})
    t0 = time.time()
    for at, fault in slot_plan(seed, int(os.environ.get("CHAOS_SLOTS", "100")), faults, slot,
                               float(os.environ.get("CHAOS_JITTER_MIN", "3"))):
        time.sleep(max(0.0, t0 + at * 60 - time.time()))
        while _active():
            _seal({"event": "slot_delayed", "fault": fault})
            time.sleep(30)
        try:
            inject(fault)
        except Exception as e:
            _seal({"event": "inject_failed", "fault": fault, "error": str(e)[:300]})


def main(argv: list[str]):
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        for f, (svc, remedy, also) in FAULTS.items():
            print(f"{f:24} service={svc:11} correct: {remedy}{' (also: ' + ', '.join(also) + ')' if also else ''}")
    elif cmd == "inject":
        print(json.dumps(inject(argv[1])))
    elif cmd == "heal":
        print(json.dumps(heal()))
    elif cmd == "serve":
        watch_resolution()
    elif cmd == "slots":
        slots()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
