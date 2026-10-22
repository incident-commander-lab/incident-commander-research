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
        assert t.outcomes[-1] in ("healthy", "ready", "has_endpoints", "ports_ok", "none", "clean", "ok", "unchanged"), name


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
