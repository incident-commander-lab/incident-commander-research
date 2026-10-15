"""Paper 2, Experiment 1: the model ladder (offline, laptop only).

Does a stronger hypothesis writer make the rule worse (the Upgrade Paradox), and does
rule v2 remove the effect at every size? Each model (qwen3 0.6B ... 14B) writes
hypotheses for the 73 RQ1 problems; the real agent (VoIAgent, unchanged) then
investigates against a FAKE CLUSTER that answers every check with the reading the real
cluster gave during the RQ1 nights.

  * Same inputs for every model: AIOpsLab's own problem text (dumped from the bench
    host, AIOpsLab commit in data/ladder/README.md) and one fixed snapshot per problem
    of `kubectl get svc` / workloads / `kubectl get pods`, taken from a catalog-v2 log.
  * Reading bank: every paid reading of every saved run of that problem (all models
    and setups), majority label per check. Readings are LABELS, not raw text (the
    saved raw text is cut at 600 characters), so the log reader is not re-run: the
    experiment varies only who writes the hypotheses. A check nobody ever ran, or one
    whose tool failed, is answered with a tool error (the agent marks it unavailable).
  * One generation per (model, problem, findings), shared by rule v1 and rule v2, so
    the two rules are compared on identical hypotheses. Cached to disk: resumable.
  * Scored with ic.bench.replay.ground_truth/score (answers no graded run ever gave
    are "unscored").

    python -m ic.bench.ladder build                     # inputs + reading bank -> data/ladder/
    python -m ic.bench.ladder run qwen3:4b [qwen3:8b …] # needs Ollama on localhost
    python -m ic.bench.ladder table
"""
from __future__ import annotations

import collections
import dataclasses
import gzip
import json
import os
import re
import sys
import time

from . import hypotheses as hyp_gen
from .agent import VoIAgent, _WORKLOADS_END
from .decide import BenchHypothesis
from .probes import tool_error
from .replay import ground_truth, parse_submission, score

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DATA = os.path.join(ROOT, "data", "ladder")
RESULTS = os.environ.get("IC_RQ1_RESULTS") or next(
    (p for p in (os.path.join(ROOT, "results", "aiopslab_full_rq1", "results"),
               os.path.join(ROOT, "..", "results", "aiopslab_full_rq1", "results"))  # worktree checkout
     if os.path.exists(p)), os.path.join(ROOT, "results", "aiopslab_full_rq1", "results"))
TOOL_ERROR = "Error from server (NotFound): no reading of this check in the RQ1 runs"
MODELS = ["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b", "qwen3:8b", "qwen3:14b"]


def _log_path(r: dict) -> str:
    return os.path.join(RESULTS, "full_logs", f"{r['model'].replace(':', '_')}__{r['policy']}__{r['problem']}.log")


def _listings(log: str) -> tuple[str, str] | None:
    """(kubectl get svc output, workloads + END marker + pods output) from a catalog-v2 log."""
    m = re.search(r"\n(NAME +TYPE +CLUSTER-IP.*?)\n\n(.*?" + _WORKLOADS_END
                  + r"\nNAME +READY .*?)\n\n", log, re.S)
    return (m.group(1) + "\n", m.group(2) + "\n") if m else None


def build(rows: list[dict], descs: dict) -> None:
    """data/ladder/inputs.json.gz and bank.json.gz."""
    pref = {("qwen3:8b", "exhaustive"): 0, ("qwen3:8b", "voi2"): 1}
    inputs = {}
    for pid in sorted({r["problem"] for r in rows}):
        cands = sorted((r for r in rows if r["problem"] == pid and r.get("catalog_version") == 2),
                       key=lambda r: (pref.get((r["model"], r["policy"]), 9), r["model"], r["policy"]))
        for r in cands:
            got = _listings(open(_log_path(r), errors="replace").read()) if os.path.exists(_log_path(r)) else None
            if got:
                inputs[pid] = {"desc": descs[pid], "svc": got[0], "pods": got[1], "task": r["task"],
                               "source": f"{r['model']}__{r['policy']}"}
                break
        else:
            raise SystemExit(f"no catalog-v2 log with listings for {pid}")
    votes: dict = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    meta: dict = {}
    for r in rows:
        for s in r.get("trace") or []:
            if isinstance(s, dict) and not s.get("free"):
                votes[r["problem"]][s["probe"]][s["outcome"]] += 1
                meta[(r["problem"], s["probe"], s["outcome"])] = s.get("eps")
        for k in r.get("unavailable_probes") or []:
            votes[r["problem"]][k]["__unavailable__"] += 1
    bank, conflicts, total = {}, 0, 0
    for pid, probes in votes.items():
        bank[pid] = {}
        for k, c in probes.items():
            (label, n), = c.most_common(1)
            total += 1
            conflicts += len(c) > 1
            bank[pid][k] = {"outcome": label, "eps": meta.get((pid, k, label)), "n": sum(c.values()),
                            "agree": round(n / sum(c.values()), 3)}
    os.makedirs(DATA, exist_ok=True)
    for name, obj in (("inputs", inputs), ("bank", bank)):
        with gzip.open(os.path.join(DATA, f"{name}.json.gz"), "wt") as f:
            json.dump(obj, f, sort_keys=True)
    print(f"inputs for {len(inputs)} problems; bank: {total} checks, {conflicts} with disagreeing readings")


def _load(name: str):
    with gzip.open(os.path.join(DATA, f"{name}.json.gz"), "rt") as f:
        return json.load(f)


class _Cache:
    """(model, problem, findings) -> generated hypotheses, persisted as JSON lines."""

    def __init__(self, path: str):
        self.path, self.d = path, {}
        if os.path.exists(path):
            for line in open(path):
                e = json.loads(line)
                self.d[(e["model"], e["problem"], e["findings"])] = e

    def generator(self, model: str, pid: str):
        def gen(problem, services, probes, observations, findings=None):
            key = (model, pid, json.dumps(findings or []) + ("|retry" if "valid service names (use exactly these)"
                                                             in observations else ""))
            if key not in self.d:
                t0 = time.monotonic()
                try:
                    hyps, meta = hyp_gen.generate(problem, services, probes, observations, findings)
                    e = {"hyps": [dataclasses.asdict(h) for h in hyps], "meta": meta}
                except Exception as ex:  # recorded, and re-raised so the agent gives up honestly
                    e = {"error": str(ex).splitlines()[-1][:300] if str(ex) else type(ex).__name__}
                from .. import llm
                e.update(model=model, problem=pid, findings=key[2], seconds=round(time.monotonic() - t0, 1),
                         usage=dict(llm.USAGE))
                self.d[key] = e
                with open(self.path, "a") as f:
                    f.write(json.dumps(e) + "\n")
            e = self.d[key]
            if "error" in e:
                raise RuntimeError(e["error"])
            return [BenchHypothesis(**h) for h in e["hyps"]], e["meta"]
        return gen


def episode(pid: str, inp: dict, bank: dict, rule_version: int, generate) -> dict:
    """One investigation by the unchanged VoIAgent against the fake cluster."""
    agent = VoIAgent("voi", rule_version=rule_version, generate=generate, log_reader="regex")
    agent.init_context(inp["desc"])
    missing = []
    reading: dict = {}

    def observe(state: str) -> None:  # inject the bank's label instead of re-reading text
        if tool_error(state):
            agent.inv.mark_unavailable(agent.pending)
            return
        agent.inv.observe(agent.pending, reading["outcome"], eps=reading.get("eps"))

    agent._observe_pending = observe
    agent.step("")
    agent.step(inp["svc"])
    agent.step(inp["pods"])
    for _ in range(60):
        if agent.phase != "investigate":
            break
        reading = bank.get(agent.pending) or {}
        if not reading or reading["outcome"] == "__unavailable__":
            missing.append(agent.pending)
            agent.step(TOOL_ERROR)
        else:
            agent.step("ok")
    agent.finalize()
    res = agent.result
    hyps = res.get("hypotheses") or {}
    llm_h = [h for h in hyps.values() if h.get("service") and not h.get("catch_all")]
    return {"submission": res.get("submission"), "paid": res.get("probes_run", 0),
            "missing": len(missing), "unavailable": len(res.get("unavailable_probes") or []),
            "stop_reason": res.get("stop_reason"), "leader": res.get("leader"),
            "p_fault": res.get("p_fault"), "final_confidence": res.get("final_confidence"),
            "llm_error": res.get("llm_error"), "regenerations": res.get("regenerations", 0),
            "n_hyps": len(llm_h),
            "preds_per_hyp": round(sum(len(h["predictions"]) for h in llm_h) / len(llm_h), 2) if llm_h else 0}


def run(models: list[str]) -> None:
    from .. import llm
    inputs, bank = _load("inputs"), _load("bank")
    rows = [json.loads(l) for l in open(os.path.join(RESULTS, "full_rq1.jsonl"))]
    gt = ground_truth(rows)
    out_path = os.path.join(DATA, "ladder_runs.jsonl")
    done = set()
    if os.path.exists(out_path):
        done = {(e["model"], e["problem"], e["rule_version"]) for e in map(json.loads, open(out_path))}
    cache = _Cache(os.path.join(DATA, "generations.jsonl"))
    for model in models:
        os.environ.update(IC_USE_LLM="1", IC_STRICT_PROVIDER="1", IC_PROVIDER="openrouter_models",
                          IC_OPENROUTER_MODELS=f"ollama:{model}", IC_LLM_TIMEOUT="600",
                          IC_OLLAMA_BASE_URL=os.environ.get("IC_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"))
        t0 = time.monotonic()
        for i, pid in enumerate(sorted(inputs), 1):
            for v in (1, 2):
                if (model, pid, v) in done:
                    continue
                llm.reset_usage()
                ep = episode(pid, inputs[pid], bank.get(pid, {}), v, cache.generator(model, pid))
                task = inputs[pid]["task"]
                ok = score(parse_submission(ep["submission"], task), task, gt[pid])
                e = {"model": model, "problem": pid, "task": task, "rule_version": v, "correct": ok, **ep}
                with open(out_path, "a") as f:
                    f.write(json.dumps(e) + "\n")
            print(f"{model} {i}/{len(inputs)} {pid}  ({(time.monotonic() - t0) / 60:.0f} min)", flush=True)
        print(f"MODEL_DONE {model}", flush=True)


def table() -> None:
    import math
    runs = [json.loads(l) for l in open(os.path.join(DATA, "ladder_runs.jsonl"))]

    def wilson(k, n, z=1.96):
        if not n:
            return float("nan"), float("nan")
        p = k / n
        d = 1 + z * z / n
        c = (p + z * z / (2 * n)) / d
        h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
        return c - h, c + h

    print(f"{'model':11} rule  {'all':>14} {'detection':>10} {'loc+analysis':>16} unscored  checks  missing  "
          f"hyps  preds/hyp  no-gen")
    for model in [m for m in MODELS if any(r["model"] == m for r in runs)]:
        for v in (1, 2):
            rs = [r for r in runs if r["model"] == model and r["rule_version"] == v]
            if not rs:
                continue
            ok = sum(r["correct"] is True for r in rs)
            det = [r for r in rs if r["task"] == "detection"]
            hard = [r for r in rs if r["task"] != "detection"]
            hk = sum(r["correct"] is True for r in hard)
            lo, hi = wilson(hk, len(hard))
            n = len(rs)
            print(f"{model:11} v{v}  {ok:3}/{n:<3}{ok / n:5.0%}  {sum(r['correct'] is True for r in det):4}/{len(det):<4}"
                  f" {hk:3}/{len(hard):<3}{hk / max(1, len(hard)):4.0%} [{lo:.0%},{hi:.0%}]"
                  f" {sum(r['correct'] is None for r in rs):6}  {sum(r['paid'] for r in rs) / n:6.1f}"
                  f"  {sum(r['missing'] for r in rs) / n:6.2f}  {sum(r['n_hyps'] for r in rs) / n:4.1f}"
                  f"  {sum(r['preds_per_hyp'] for r in rs) / n:9.1f}  {sum(bool(r['llm_error']) for r in rs):6}")
    print("always-'Yes' baseline: detection 29/32, overall 29/73 (40%), loc+analysis 0/41")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "table"
    if cmd == "build":
        rows = [json.loads(l) for l in open(os.path.join(RESULTS, "full_rq1.jsonl"))]
        build(rows, json.load(open(sys.argv[2])))
    elif cmd == "run":
        run(sys.argv[2:] or MODELS)
    else:
        table()
