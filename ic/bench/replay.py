"""Offline replay: re-decide saved VoI runs under a different rule version.

Each saved run stores its hypotheses (with predictions) and every reading it took,
in order. The replay rebuilds the Investigation, feeds the same readings back, and
lets the rule decide again — when to stop and what to answer — without touching a
cluster or an LLM. It cannot take readings the original run never took: if the new
rule wants to keep checking past the original's last reading, the replay stops there
(so it is a conservative estimate), and it follows the original probe order.

Answers are scored against ground truth recovered from the graded runs of all
conditions; an answer no graded run ever gave is reported as "unscored".

    python -m ic.bench.replay results/aiopslab_full_rq1/results/full_rq1.jsonl
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from typing import Optional

from .agent import catch_alls
from .decide import BenchHypothesis, Investigation
from .probes import instances


def _services(run: dict) -> list[str]:
    keys = set()
    for h in run["hypotheses"].values():
        keys |= set(h["predictions"])
    keys |= {s["probe"] for s in run["trace"]}
    return sorted({k.split(":", 1)[1] for k in keys})


def replay(run: dict, rule_version: int) -> dict:
    services = _services(run)
    probes = instances(services)
    hyps = [BenchHypothesis(hid, h["service"], h["fault_type"], h["system_level"], "", h["prior"],
                            {k: v for k, v in h["predictions"].items() if k in probes})
            for hid, h in run["hypotheses"].items() if not h.get("catch_all")]
    if rule_version >= 2:
        hyps += catch_alls(services, probes)
    inv = Investigation(hyps, probes, policy="voi", lam=run.get("lambda", 0.02), rule_version=rule_version)
    stopped_early = False
    for s in run["trace"]:
        if s["probe"] not in probes:
            continue
        if not s.get("free") and inv.next_probe() is None:
            stopped_early = True
            break
        inv.observe(s["probe"], s["outcome"], free=bool(s.get("free")), eps=s.get("eps"))
    return {"answer": answer(inv, run["task"], rule_version), "paid": inv.paid_steps(),
            "stopped_early": stopped_early, "p_fault": inv.p_fault(), "leader": inv.leader().id}


def answer(inv: Investigation, task: str, rule_version: int):
    """Mirror of VoIAgent._answer, returning plain values."""
    lead = inv.leader()
    faulty = lead.service is not None
    if task == "detection":
        return ("Yes" if inv.p_fault() > 0.5 else "No") if rule_version >= 2 else ("Yes" if faulty else "No")
    if rule_version >= 2:
        m = inv.service_marginals()
        svc = max(m, key=m.get) if m else None
        if task == "analysis":
            specific = [h for h in inv.hyps.values() if h.service is not None and not h.catch_all]
            same = [h for h in specific if h.service == svc] or specific
            if not same:
                return None
            best = max(same, key=lambda h: inv.posteriors[h.id])
            return (best.system_level, best.fault_type)
        return [svc] if svc else []
    if task == "analysis":
        return (lead.system_level, lead.fault_type) if faulty else None
    return [lead.service] if faulty else []


def parse_submission(sub, task: str):
    if not isinstance(sub, str):
        return None
    m = re.search(r"submit\((.*)\)", sub.strip(), re.S)
    body = m.group(1).strip() if m else "None"
    try:
        v = ast.literal_eval(body) if body not in ("None", "") else None
    except (ValueError, SyntaxError):
        try:
            v = json.loads(body)
        except ValueError:
            return None
    if task == "analysis" and isinstance(v, dict):
        return (v.get("system_level"), v.get("fault_type"))
    return v


def ground_truth(rows: list[dict]) -> dict:
    """problem -> {"detection": "Yes"/"No"} | {"right": set, "wrong": set} | analysis level/type sets."""
    gt: dict = defaultdict(lambda: {"right": set(), "wrong": set(), "lvl": set(), "typ": set(),
                                    "not_lvl": set(), "not_typ": set()})
    for r in rows:
        a, t, p = r.get("aiopslab_results") or {}, r.get("task"), r.get("problem")
        if not a or "submission" not in r or r.get("llm_error"):
            continue
        sub = parse_submission(r["submission"], t)
        g = gt[p]
        if t == "detection" and "Detection Accuracy" in a:
            ok = a["Detection Accuracy"] == "Correct"
            g["detection"] = sub if ok else ("No" if sub == "Yes" else "Yes")
        elif t == "localization":
            for svc in (sub or []):
                (g["right"] if a.get("success") else g["wrong"]).add(svc)
            if not sub and not a.get("success"):
                g["wrong"].add(None)
        elif t == "analysis" and isinstance(sub, tuple):
            (g["lvl"] if a.get("system_level_correct") else g["not_lvl"]).add(sub[0])
            (g["typ"] if a.get("fault_type_correct") else g["not_typ"]).add(sub[1])
    return gt


def score(ans, task: str, g: dict) -> Optional[bool]:
    """True/False, or None when no graded run tells us."""
    if task == "detection":
        return None if "detection" not in g else ans == g["detection"]
    if task == "localization":
        if not ans:
            return False
        svc = ans[0]
        return True if svc in g["right"] else (False if svc in g["wrong"] or g["right"] else None)
    if task == "analysis":
        if ans is None:
            return False
        lvl, typ = ans
        if lvl in g["not_lvl"] or typ in g["not_typ"]:
            return False
        if lvl in g["lvl"] and typ in g["typ"]:
            return True
        return None
    return None


def main(path: str) -> None:
    rows = [json.loads(l) for l in open(path)]
    gt = ground_truth(rows)
    for model in sorted({r.get("model") for r in rows if r.get("policy") == "voi"}):
        runs = [r for r in rows if r.get("model") == model and r.get("policy") == "voi" and "trace" in r]
        print(f"\n{model}: {len(runs)} runs")
        for v in (1, 2):
            tally = defaultdict(lambda: [0, 0, 0])  # task -> [correct, wrong, unscored]
            same_as_saved = 0
            paid = 0
            for r in runs:
                out = replay(r, v)
                paid += out["paid"]
                s = score(out["answer"], r["task"], gt[r["problem"]])
                tally[r["task"]][0 if s else (1 if s is False else 2)] += 1
                if out["answer"] == parse_submission(r["submission"], r["task"]):
                    same_as_saved += 1
            tot = [sum(x[i] for x in tally.values()) for i in range(3)]
            per = "  ".join(f"{t} {c}/{c + w + u}" for t, (c, w, u) in sorted(tally.items()))
            print(f"  rule v{v}: correct {tot[0]}/{len(runs)} ({tot[0] / len(runs):.0%}), unscored {tot[2]}, "
                  f"checks/run {paid / len(runs):.1f}, same answer as saved run {same_as_saved}/{len(runs)}  [{per}]")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/aiopslab_full_rq1/results/full_rq1.jsonl")
