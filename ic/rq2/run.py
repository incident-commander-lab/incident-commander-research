"""Run RQ2 policies on paired simulated incidents across customer-impact levels.

    python -m ic.rq2.run [--n 2000]

Each (cause, seed) incident is replayed for every policy with the same random
seed, so policies face the same check noise where their choices coincide.
"""
from __future__ import annotations

import argparse
import random
import statistics

from .policies import POLICIES, run_episode
from .sim import CAUSES, Episode, correct_handling

IMPACTS = {"low (r=0.25)": 0.25, "moderate (r=1)": 1.0, "high (r=4)": 4.0, "critical (r=16)": 16.0}


def run(n: int = 2000) -> list[dict]:
    rows = []
    for label, r in IMPACTS.items():
        for pol in POLICIES:
            eps = [run_episode(Episode(c, r, random.Random(hash((c, i)) & 0xffffffff)), pol)
                   for c in CAUSES for i in range(n)]
            by = {c: [e for e in eps if e.cause == c] for c in CAUSES}
            pairs = [correct_handling(by["deploy"][i]) and correct_handling(by["db"][i]) for i in range(n)]
            rows.append({
                "impact": label, "policy": pol,
                "cost": statistics.mean(e.cost for e in eps),
                "ttm": statistics.mean(e.time_to_mitigation for e in eps),
                "correct": statistics.mean(correct_handling(e) for e in eps),
                "paired": statistics.mean(pairs),
                "harmful": statistics.mean(e.harmful > 0 for e in eps),
                "escalated_when_fixable": statistics.mean(e.resolved_by == "human" for e in by["deploy"] + by["dep"]),
                "acted_when_not_fixable": statistics.mean(e.harmful > 0 for e in by["db"] + by["unknown"]),
                "checks": statistics.mean(sum(1 for k, *_ in e.log if k == "check") for e in eps),
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    rows = run(ap.parse_args().n)
    head = (f"{'impact':20} {'policy':11} {'cost':>8} {'time(s)':>8} {'correct':>8} {'paired':>7} "
            f"{'harmful':>8} {'esc|fixable':>12} {'act|unfixable':>14} {'checks':>7}")
    print(head); print("-" * len(head))
    for r in rows:
        print(f"{r['impact']:20} {r['policy']:11} {r['cost']:8.0f} {r['ttm']:8.0f} {r['correct']:8.0%} "
              f"{r['paired']:7.0%} {r['harmful']:8.0%} {r['escalated_when_fixable']:12.0%} "
              f"{r['acted_when_not_fixable']:14.0%} {r['checks']:7.2f}")


if __name__ == "__main__":
    main()
