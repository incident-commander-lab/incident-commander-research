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
