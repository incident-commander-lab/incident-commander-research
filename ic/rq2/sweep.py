"""RQ2 sensitivity sweep: how robust is the expected-cost policy when the agent's
model of the world is wrong?

  1. Unknown causes: the true share of incidents with a cause outside every
     hypothesis, against the 'other' prior the agent assumes.
  2. Check noise: real check error rates scaled up or down, while the agent
     keeps assuming the nominal rates.

Reported as the paired cost difference of ours against each baseline (negative
means ours is cheaper), with a 95% confidence interval.

    python -m ic.rq2.sweep [--n 1200] [--impact 4]
"""
from __future__ import annotations

import argparse
import math
import random
import statistics

import ic.rq2.policies as P
from .sim import Episode

FIXABLE = ("deploy", "dep", "db")
BASELINES = ("conf70", "diag_first", "gira")


def mix(unknown_share: float) -> dict[str, float]:
    return {**{c: (1 - unknown_share) / 3 for c in FIXABLE}, "unknown": unknown_share}


def paired(policy_a, policy_b, causes: dict, r: float, n: int, eps_scale: float, tag: str):
    rng = random.Random(hash(tag) & 0xffffffff)
    diffs = []
    for i in range(n):
        cause = rng.choices(list(causes), weights=list(causes.values()))[0]
        seed = rng.getrandbits(32)
        a = P.run_episode(Episode(cause, r, random.Random(seed), eps_scale=eps_scale), policy_a).cost
        b = P.run_episode(Episode(cause, r, random.Random(seed), eps_scale=eps_scale), policy_b).cost
        diffs.append(a - b)
    m = statistics.mean(diffs)
    h = 1.96 * statistics.stdev(diffs) / math.sqrt(n)
    return m, h


def fmt(m, h):
    mark = "*" if abs(m) > h else " "      # * = the 95% interval excludes zero
    return f"{m:+7.0f}±{h:<4.0f}{mark}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--impact", type=float, default=4.0)
    a = ap.parse_args()
    r, n = a.impact, a.n

    print(f"\n1) Unknown causes (impact r={r}): ours − baseline cost per incident; negative = ours cheaper; * = significant")
    print(f"{'true unknown share':>19} {'agent assumes':>14} " + " ".join(f"{b:>14}" for b in BASELINES))
    for true in (0.0, 0.10, 0.25, 0.40):
        for assumed in (0.05, 0.10, 0.25, 0.40):
            P.OTHER_PRIOR = assumed; P._MEMO.clear()
            cells = [fmt(*paired("ours", b, mix(true), r, n, 1.0, f"u{true}{assumed}{b}")) for b in BASELINES]
            print(f"{true:19.0%} {assumed:14.0%} " + " ".join(f"{c:>14}" for c in cells))

    print(f"\n2) Check noise (impact r={r}, unknown share 10%, agent assumes 10%): real noise = nominal × scale")
    print(f"{'noise scale':>12} " + " ".join(f"{b:>14}" for b in BASELINES))
    P.OTHER_PRIOR = 0.10; P._MEMO.clear()
    for scale in (0.5, 1.0, 2.0, 3.0):
        cells = [fmt(*paired("ours", b, mix(0.10), r, n, scale, f"e{scale}{b}")) for b in BASELINES]
        print(f"{scale:12.1f} " + " ".join(f"{c:>14}" for c in cells))


if __name__ == "__main__":
    main()
