"""Cluster agent: same frozen decision core as the Compose agent; RQ1 checks run as-is."""
import ast
import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "cluster", "agent"))


def _fn_src(path, name):
    tree = ast.parse(open(path).read())
    return next(ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


@pytest.mark.parametrize("name", ["_remedy_entropy", "_best_remedy_check"])
def test_decision_core_is_verbatim(name):
    assert _fn_src(os.path.join(ROOT, "testbed/ops/testbed_ops/agent.py"), name) == \
        _fn_src(os.path.join(ROOT, "cluster/agent/ic_cluster/agent.py"), name)


def test_rq1_checks_run_their_own_kubectl_command(monkeypatch):
    from ic_cluster import probes
    seen = []
    monkeypatch.setattr(probes, "_sh", lambda cmd, timeout=30: seen.append(cmd) or "")
    p = probes.instances()["pod_health:juice-shop"]
    probes.run_bench_call(p.call("shop"), "shop", "juice-shop")
    assert seen == ["kubectl get pods -n shop --no-headers | grep juice-shop"]
    probes.run_bench_call(probes.instances()["log_signature:assistant"].call("shop"), "shop", "assistant")
    assert seen[-1].startswith("kubectl logs -n shop deploy/assistant")


def test_catalog_contains_every_rq1_template():
    from ic.bench import probes as bench
    from ic_cluster import probes
    keys = probes.instances()
    for t in bench.TEMPLATES:
        assert f"{t}:juice-shop" in keys and f"{t}:assistant" in keys


def test_metric_labels():
    from ic_cluster import probes as P
    assert P._lbl_cpu(json.dumps({"cpu_vs_limit_max_3m": 0.97}), "x") == "saturated"
    assert P._lbl_cpu(json.dumps({"cpu_vs_limit_max_3m": None}), "x") is None          # no data: unavailable
    assert P._lbl_mem(json.dumps({"memory_vs_limit": 0.5, "oom_restarts_15m": 1}), "x") == "oom_killed"
    assert P._lbl_mem(json.dumps({"memory_vs_limit": 0.9, "oom_restarts_15m": None}), "x") == "near_limit"
    assert P._lbl_latency(json.dumps({"p95_s": None}), "x") == "ok"                     # no traffic, nothing slow
    assert P._lbl_latency(json.dumps({"p95_s": 6.0}), "assistant") == "ok"               # an LLM answer takes seconds
    assert P._lbl_latency(json.dumps({"p95_s": 6.0}), "juice-shop") == "slow"
    assert P._lbl_errors(json.dumps({"rps": 10, "err_rps": 1}), "x") == "errors"
    now = "2026-10-18T10:00:00Z"
    assert P._lbl_change(json.dumps({"revisions": [[2, "2026-10-18T09:50:00Z"]], "now": now}), "x") == "changed"
    assert P._lbl_change(json.dumps({"revisions": [[1, "2026-10-18T09:50:00Z"]], "now": now}), "x") == "unchanged"
    assert P._lbl_change(json.dumps({"revisions": [[3, "2026-10-18T08:00:00Z"]], "now": now}), "x") == "unchanged"
    for name, t in P.TEMPLATES.items():   # the rule relies on the last outcome being the healthy one
        assert t.outcomes[-1] in ("healthy", "ready", "has_endpoints", "ports_ok", "none", "clean", "ok", "unchanged",
                                  "flowing", "connected", "clear"), name   # Pulse names, as in Compose


def test_assistant_dead_model_address_reads_as_connection_errors():
    # 21 Oct shakedown: a wrong model address fails fast and, before the assistant logged the reason,
    # left only "503" access lines (log_signature=clean). Its own error line must read as the cause.
    from ic.bench.probes import label_log_signature
    src = open(os.path.join(ROOT, "cluster/assistant/app.py")).read()
    assert 'log.error("upstream unreachable: %s: %s (%s)"' in src
    line = ("2026-10-21 08:01:02,123 ERROR assistant: upstream unreachable: ConnectError: "
            "[Errno -2] Name or service not known (http://model-gateway.ai.svc:11434/api/chat)")
    access = 'INFO:     10.42.0.9:51234 - "POST /assistant/ask HTTP/1.1" 503 Service Unavailable'
    assert label_log_signature("\n".join([line, access] * 10), "assistant") == "connection_errors"
    assert label_log_signature("\n".join([access] * 20), "assistant") == "clean"


def test_plain_llm_agent_runs_checks_then_acts_through_the_shared_safety_net(monkeypatch, tmp_path):
    # RQ3 setup 2 on the cluster: the LLM picks checks and the fix; OPA, threshold and verification are shared
    from ic_cluster import agent as A, llm_agent as L, probes
    monkeypatch.setattr(A, "OUT", tmp_path)
    monkeypatch.setattr(A, "AUTONOMY", "act")
    monkeypatch.setattr(probes, "run", lambda p: ("raw", "errors" if p.key == "error_rate:assistant" else p.outcomes[-1]))
    replies = iter([L._Step(thought="t", next="check", check="error_rate:assistant"),
                    L._Step(thought="t", next="check", check="error_rate:assistant"),   # repeat: handed back, not re-run
                    L._Step(thought="t", next="decide", service="assistant", cause="bad release",
                            remedy="rollout_undo:assistant", confidence=0.9)])
    monkeypatch.setattr(L.llm, "json_call", lambda *a, **k: next(replies))
    acted = []
    monkeypatch.setattr(A, "_act", lambda rec, remedy, p, pr, approved: acted.append((remedy, p)))
    rec = L.handle_llm([{"name": "ShopErrors", "summary": "assistant 5xx"}])
    assert rec["agent_kind"] == "llm" and rec["investigation"]["checks_paid"] == 1
    assert rec["investigation"]["errors"] == ["repeated check error_rate:assistant"]
    assert rec["diagnosis"]["decision"] == "act" and acted == [("rollout_undo:assistant", 0.9)]
    assert (tmp_path / f"{rec['id']}.json").exists()
    assert A.PLATFORM in L._system(probes.instances()) and A.SYMPTOMS in L._system(probes.instances())


def test_rule_and_llm_agents_are_told_the_same_about_the_system():
    from ic_cluster import agent as A, llm_agent as L, probes
    pr = probes.instances()
    for text in (A.PLATFORM, A.SYMPTOMS):
        assert text in A._system(pr) and text in L._system(pr)


def test_attack_signature_reads_traefik_access_logs(monkeypatch):
    from ic_cluster import probes
    attack = '10.42.0.31 - - [22/Oct/2026:20:01:02 +0000] "GET /rest/products/search?q=%27%20OR%201%3D1--%20 HTTP/1.1" 500 12 "-" "scan/1.0" 41 "shop-juice-shop@kubernetes" "http://10.42.1.5:3000" 3ms'
    trav = '10.42.0.31 - - [22/Oct/2026:20:01:03 +0000] "GET /ftp/..%2F..%2Fetc%2Fpasswd HTTP/1.1" 403 12 "-" "scan/1.0" 42 "shop-juice-shop@kubernetes" "http://10.42.1.5:3000" 2ms'
    user = '10.42.0.20 - - [22/Oct/2026:20:01:03 +0000] "GET /rest/products/search?q=apple HTTP/1.1" 200 900 "-" "Mozilla" 43 "shop-juice-shop@kubernetes" "http://10.42.1.5:3000" 9ms'
    blocked = attack.replace("shop-juice-shop@kubernetes", "shop-ic-block-10-42-0-31@kubernetescrd")
    monkeypatch.setattr(probes, "_sh", lambda cmd, timeout=30: "\n".join([attack] * 15 + [trav] * 10 + [user] * 40))
    p = probes.instances()["attack_signature:juice-shop"]
    raw, label = probes.run(p)
    assert label == "attack" and json.loads(raw)["top_client"] == "10.42.0.31" and probes.LAST_ATTACKER["ip"] == "10.42.0.31"
    assert p.template.cost == 2.0
    monkeypatch.setattr(probes, "_sh", lambda cmd, timeout=30: "\n".join([blocked] * 50 + [user] * 40))
    assert probes.run(p)[1] == "clean"               # requests our block answered are not a new attack
    monkeypatch.setattr(probes, "_sh", lambda cmd, timeout=30: "")
    assert probes.run(p)[1] is None                  # no access log: unreadable, not evidence


def test_block_client_needs_an_identified_attacker_and_a_human(monkeypatch):
    from ic_cluster import actions, probes
    probes.LAST_ATTACKER.clear()
    with pytest.raises(RuntimeError):
        actions.execute("block_client", reason="test")
    applied, deleted = [], []
    probes.LAST_ATTACKER.update(ip="10.42.0.31")
    monkeypatch.setattr(actions, "_apply", lambda m: applied.append(m))
    monkeypatch.setattr(actions, "_k", lambda *a: deleted.append(a) if a[0] == "delete" else "rancher/mirrored-library-traefik:2.11.18")
    undo = actions.execute("block_client", reason="incident x")
    route = applied[1]["spec"]["routes"][0]
    assert applied[0]["spec"] == {"ipWhiteList": {"sourceRange": ["127.0.0.1/32"]}}
    assert route["match"] == "ClientIP(`10.42.0.31`)" and route["middlewares"] == [{"name": "ic-deny-all"}]
    undo()
    assert deleted and deleted[0][:2] == ("delete", "ingressroute.traefik.io")
    policy = open(os.path.join(ROOT, "cluster/agent/policy.rego")).read()
    assert 'input.action == "block_client"' in policy and "not input.approved" in policy


def _agent_view(apps: str) -> dict:
    """What the agent knows under APPS=<apps> (module-level settings, so in a fresh interpreter)."""
    import subprocess
    code = ("import json; from ic_cluster import agent as A, actions, probes; "
            "print(json.dumps({'platform': A.PLATFORM, 'services': list(probes.APPLIES), "
            "'remedies': sorted(actions.CATALOG), 'checks': sorted(probes.instances())}))")
    env = {**os.environ, "APPS": apps, "PYTHONPATH": os.pathsep.join([os.path.join(ROOT, "cluster", "agent"), ROOT])}
    return json.loads(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout)


def test_shop_only_agent_is_unchanged_by_pulse():
    v = _agent_view("shop")
    assert v["services"] == ["juice-shop", "assistant"]
    assert v["platform"].startswith("a web\nshop running on Kubernetes") and "pulse" not in v["platform"].lower()
    assert not any(r.endswith((":worker", ":collector", ":api", ":postgres", ":redis")) for r in v["remedies"])


def test_pulse_in_the_cluster_has_its_checks_and_fixes():
    v = _agent_view("shop,pulse")
    assert v["services"] == ["juice-shop", "assistant", "collector", "worker", "api", "redis", "postgres"]
    for r in ("rollout_undo:worker", "rollout_undo:collector", "restart:worker", "restart:collector", "scale_out:api",
              "kill_blocking_sessions:postgres", "restore_redis_maxmemory:redis", "block_client"):
        assert r in v["remedies"], r
    for c in ("throughput:worker", "stream_lag:worker", "source_connected:collector", "db_locks:postgres",
              "redis_memory:redis", "endpoints:api", "log_signature:worker"):
        assert c in v["checks"], c
    assert "endpoints:worker" not in v["checks"]          # no Service object: the check would always say "none"
    assert "data pipeline" in v["platform"] and "web\nshop" in v["platform"]
    policy = open(os.path.join(ROOT, "cluster/agent/policy.rego")).read()
    assert all(f'"{r}"' in policy for r in v["remedies"] if r != "none")


def test_every_pulse_fault_has_a_fix_the_agent_can_take():
    import ast
    src = open(os.path.join(ROOT, "cluster/chaos/chaos.py")).read()
    faults = ast.literal_eval(src[src.index("FAULTS = {") + len("FAULTS = "):src.index("\n}\n", src.index("FAULTS = {")) + 2])
    remedies = _agent_view("shop,pulse")["remedies"]
    for f, (svc, remedy, also) in faults.items():
        assert remedy in remedies, (f, remedy)
