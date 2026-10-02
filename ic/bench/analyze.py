"""Summarise benchmark results (JSONL from run_aiopslab) into the paper's table.

    python -m ic.bench.analyze results/pilot.jsonl [more.jsonl ...]

One row per (model, condition). Beyond accuracy and cost, three metrics answer
the obvious objection to a cheaper agent — "it only uses fewer probes because
it gives up early":

  * premature stop — wrong answer although the agent stopped by its OWN rule
    (not the step budget, not an LLM failure). A cheap agent that stops early
    and wrong shows up here.
  * repeated actions — share of actions that repeat an earlier identical one
    (redundant investigation; the trajectory study's failure mode).
  * Brier score — calibration of the agent's final confidence against
    correctness (agents that report a posterior only).

Accuracy carries a 95% Wilson interval: with a handful of problems, the
interval is the honest headline, not the point estimate.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict

ORDER = ["react", "llm_choose", "voi", "random", "exhaustive"]
LABEL = {"react": "A plain LLM", "llm_choose": "B LLM chooses", "voi": "C VoI (ours)",
         "random": "D random", "exhaustive": "E exhaustive"}
OWN_STOPS = {None, "no_probe_worth_its_cost", "confident", "llm_stop", "no_probes_left"}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def actions_of(r: dict) -> list[str]:
    if "actions" in r:                                       # plain LLM agent
        return r["actions"]
    trace = r.get("trace", [])
    if trace and isinstance(trace[0], str):                  # older plain-LLM rows: raw replies
        calls = [re.search(r"```[a-zA-Z]*\s*\n(.*?)\n\s*```", t, re.DOTALL) for t in trace]
        return [m.group(1).strip() for m in calls if m and not m.group(1).strip().startswith("submit")]
    return [s["probe"] for s in trace if not s.get("free")]


def success(r: dict) -> bool:
    return bool((r.get("aiopslab_results") or {}).get("success"))


def summarise(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("model") or "?", r["policy"])].append(r)
    out = []
    for (model, policy), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], ORDER.index(kv[0][1])
                                                                     if kv[0][1] in ORDER else 99)):
        n = len(rs)
        ok = sum(success(r) for r in rs)
        lo, hi = wilson(ok, n)
        failed_llm = [r for r in rs if r.get("llm_error")]
        valid = [r for r in rs if not r.get("llm_error")]
        own = [r for r in valid if r.get("stop_reason") in OWN_STOPS]
        acts = [actions_of(r) for r in valid]
        n_acts = sum(len(a) for a in acts)
        repeats = sum(len(a) - len(set(a)) for a in acts)
        conf = [(r["final_confidence"], success(r)) for r in valid if r.get("final_confidence") is not None]
        usage = [r.get("llm_usage") or {} for r in rs]
        mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
        out.append({
            "model": model, "condition": LABEL.get(policy, policy), "n": n,
            "accuracy": ok / n if n else float("nan"), "acc_ci95": (lo, hi),
            "probes": mean([len(a) for a in acts]),
            "llm_calls": mean([u.get("calls", 0) for u in usage]),
            "tokens_in": mean([u.get("input_tokens", 0) for u in usage]),
            "tokens_out": mean([u.get("output_tokens", 0) for u in usage]),
            "seconds": mean([r.get("wall_seconds", 0) for r in rs]),
            "repeated_actions": repeats / n_acts if n_acts else 0.0,
            "premature_stop": (sum(not success(r) for r in own) / len(own)) if own else float("nan"),
            "budget_hits": sum(r.get("stop_reason") == "budget" for r in valid),
            "llm_failures": len(failed_llm),
            "brier": mean([(c - s) ** 2 for c, s in conf]) if conf else None,
        })
    return out


def print_table(summary: list[dict]) -> None:
    head = (f"{'model':26} {'condition':15} {'n':>3} {'acc':>5} {'95% CI':>13} {'probes':>6} "
            f"{'llm':>4} {'tok_in':>7} {'tok_out':>7} {'secs':>5} {'repeat':>6} {'prem.stop':>9} "
            f"{'brier':>6} {'llm_fail':>8}")
    print(head)
    print("-" * len(head))
    for s in summary:
        lo, hi = s["acc_ci95"]
        brier = f"{s['brier']:.3f}" if s["brier"] is not None else "-"
        prem = f"{s['premature_stop']:.0%}" if s["premature_stop"] == s["premature_stop"] else "-"
        print(f"{s['model'][:26]:26} {s['condition']:15} {s['n']:>3} {s['accuracy']:>5.0%} "
              f"{f'[{lo:.0%},{hi:.0%}]':>13} {s['probes']:>6.1f} {s['llm_calls']:>4.1f} "
              f"{s['tokens_in']:>7.0f} {s['tokens_out']:>7.0f} {s['seconds']:>5.0f} "
              f"{s['repeated_actions']:>6.0%} {prem:>9} {brier:>6} {s['llm_failures']:>8}")


def main(paths: list[str]) -> None:
    rows = [json.loads(line) for p in paths for line in open(p) if line.strip()]
    print_table(summarise(rows))


if __name__ == "__main__":
    main(sys.argv[1:] or ["results/pilot.jsonl"])
