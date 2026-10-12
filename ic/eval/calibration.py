"""Calibration and selective prediction on saved RQ1 runs (offline, no cluster, no LLM).

    python -m ic.eval.calibration data/rq1-results/2026-10-11/full_rq1.jsonl.gz [--out DIR]

Question: if each method may abstain (escalate to a human) whenever its confidence
is below a threshold, how accurate is it on the incidents it keeps? A method whose
confidence ranks its own right answers above its wrong ones gains accuracy as it
abstains; a method whose confidence is noise does not.

Confidence = the method's probability that its SUBMITTED answer is right, read from
the saved posterior (not the leader's posterior, which can differ from the answer
under rule v2 and under LLM-picks):

  * detection     P(fault) if it answered "Yes", else 1 − P(fault)
  * localization  P(fault in the submitted service | some fault)
  * analysis      P(submitted (system level, fault type) | some fault), summed over
                  the specific hypotheses that name it (catch-alls say "Unknown")

Localization and analysis condition on "some fault" because AIOpsLab guarantees one
in those tasks. An empty or unparsable submission counts as wrong with confidence 0.
Plain-LLM (ReAct) rows carry no confidence yet: they appear with accuracy only, until
confidence is elicited post hoc from their saved trajectories.

Metrics per (model, policy):
  * Brier, ECE (10 equal-width bins)
  * AURC   area under the risk–coverage curve (lower is better)
  * E-AURC AURC minus the best AURC possible at the same accuracy, so methods with
           different accuracies compare on ranking quality alone
  * selective accuracy at 50% and 80% coverage, and when abstaining below τ = 0.5
    and τ = 0.78 (the live test bed's act threshold)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Optional

from ic.bench.analyze import ORDER, LABEL, success, wilson
from ic.bench.replay import parse_submission

THRESHOLDS = (0.5, 0.78)
COLOR = {"llm_choose": "#1f77b4", "voi": "#ff7f0e", "voi2": "#2ca02c", "random": "#d62728",
         "exhaustive": "#9467bd"}  # one colour per condition across every panel
COVERAGES = (0.5, 0.8)


def load(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt", encoding="utf-8") as f:
            rows += [json.loads(line) for line in f if line.strip()]
    return rows


def answer_confidence(r: dict) -> Optional[float]:
    """P(submitted answer is right) under the run's final posterior; None if the run
    kept no posterior (plain LLM agent)."""
    post, hyps = r.get("posteriors"), r.get("hypotheses")
    if not post or not hyps:
        return None
    return confidence(post, hyps, r["task"], parse_submission(r.get("submission"), r["task"]))


def confidence(post: dict, hyps: dict, task: str, ans) -> Optional[float]:
    """P(answer is right) under posterior `post`; `ans` as parse_submission returns it."""
    service = {hid: (hyps.get(hid) or {}).get("service") for hid in post}
    p_fault = sum(p for hid, p in post.items() if service[hid] is not None)
    if task == "detection":
        if ans not in ("Yes", "No"):
            return 0.0
        return p_fault if ans == "Yes" else 1.0 - p_fault
    if p_fault <= 0:
        return 0.0
    if task == "localization":
        if not ans or not isinstance(ans, list):
            return 0.0
        return sum(p for hid, p in post.items() if service[hid] == ans[0]) / p_fault
    if task == "analysis":
        if not isinstance(ans, tuple):
            return 0.0
        mass = sum(p for hid, p in post.items()
                   if not hyps[hid].get("catch_all") and service[hid] is not None
                   and (hyps[hid].get("system_level"), hyps[hid].get("fault_type")) == ans)
        return mass / p_fault
    return None


# --------------------------------------------------------------------------- #
def risk_coverage(pairs: list[tuple[float, bool]]) -> list[tuple[float, float]]:
    """(coverage, risk) after keeping the k most confident runs, k = 1..n. Ties in
    confidence are kept or dropped together, so the curve does not depend on row order."""
    s = sorted(pairs, key=lambda cs: -cs[0])
    n, out, wrong, i = len(s), [], 0, 0
    while i < n:
        j = i
        while j < n and s[j][0] == s[i][0]:
            wrong += not s[j][1]
            j += 1
        out.append((j / n, wrong / j))
        i = j
    return out


def aurc(pairs: list[tuple[float, bool]]) -> float:
    """Mean risk over coverage levels 1/n..1 (ties share the risk of their block)."""
    s = sorted(pairs, key=lambda cs: -cs[0])
    n, total, wrong, i = len(s), 0.0, 0, 0
    while i < n:
        j = i
        while j < n and s[j][0] == s[i][0]:
            j += 1
        block_wrong = sum(not s[k][1] for k in range(i, j))
        # inside a tie block, risk is interpolated linearly (expected order is random)
        for k in range(i, j):
            total += (wrong + block_wrong * (k - i + 1) / (j - i)) / (k + 1)
        wrong += block_wrong
        i = j
    return total / n


def optimal_aurc(n: int, n_right: int) -> float:
    """AURC of a perfect ranker: every right answer kept before any wrong one."""
    return sum(max(0, k - n_right) / k for k in range(1, n + 1)) / n


def selective_at_coverage(pairs, coverage: float) -> float:
    s = sorted(pairs, key=lambda cs: -cs[0])
    k = max(1, round(coverage * len(s)))
    return sum(ok for _, ok in s[:k]) / k


def selective_at_threshold(pairs, tau: float) -> tuple[float, float]:
    """(coverage, accuracy on kept runs) when abstaining below τ."""
    kept = [ok for c, ok in pairs if c >= tau]
    return len(kept) / len(pairs), (sum(kept) / len(kept) if kept else float("nan"))


def brier(pairs) -> float:
    return sum((c - ok) ** 2 for c, ok in pairs) / len(pairs)


def ece(pairs, bins: int = 10) -> float:
    b: dict[int, list] = defaultdict(list)
    for c, ok in pairs:
        b[min(bins - 1, int(c * bins))].append((c, ok))
    return sum(len(v) / len(pairs) * abs(sum(c for c, _ in v) / len(v) - sum(o for _, o in v) / len(v))
               for v in b.values())


def bootstrap_ci(pairs, stat, reps: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    vals = sorted(stat([rng.choice(pairs) for _ in pairs]) for _ in range(reps))
    return vals[int(0.025 * reps)], vals[int(0.975 * reps) - 1]


# --------------------------------------------------------------------------- #
def summarise(rows: list[dict]) -> tuple[list[dict], dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("model") or "?", r["policy"])].append(r)
    table, curves = [], {}
    for (model, policy), rs in sorted(groups.items(), key=lambda kv: (
            kv[0][0], ORDER.index(kv[0][1]) if kv[0][1] in ORDER else 99)):
        oks = [success(r) for r in rs]
        n, k = len(rs), sum(oks)
        lo, hi = wilson(k, n)
        row = {"model": model, "condition": LABEL.get(policy, policy), "policy": policy, "n": n,
               "accuracy": k / n, "acc_lo": lo, "acc_hi": hi}
        confs = [answer_confidence(r) for r in rs]
        if all(c is not None for c in confs):
            pairs = list(zip(confs, oks))
            a = aurc(pairs)
            e_lo, e_hi = bootstrap_ci(pairs, lambda p: aurc(p) - optimal_aurc(len(p), sum(o for _, o in p)))
            row.update({"mean_conf": sum(confs) / n, "brier": brier(pairs), "ece": ece(pairs),
                        "aurc": a, "e_aurc": a - optimal_aurc(n, k), "e_aurc_lo": e_lo, "e_aurc_hi": e_hi})
            for cov in COVERAGES:
                row[f"acc@cov{int(cov * 100)}"] = selective_at_coverage(pairs, cov)
            for tau in THRESHOLDS:
                cov, acc = selective_at_threshold(pairs, tau)
                row[f"cov@tau{tau}"], row[f"acc@tau{tau}"] = cov, acc
            curves[(model, policy)] = risk_coverage(pairs)
        table.append(row)
    return table, curves


def fmt(v, pct=True) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:.0%}" if pct else f"{v:.3f}"


def markdown(table: list[dict]) -> str:
    head = ("| model | condition | n | accuracy [95% CI] | mean conf | Brier | ECE | AURC | E-AURC [95% CI] "
            "| acc @50% cov | acc @80% cov | τ=0.5: cov / acc | τ=0.78: cov / acc |")
    lines = [head, "|" + "---|" * 13]
    for r in table:
        has = "aurc" in r
        lines.append("| " + " | ".join([
            r["model"], r["condition"], str(r["n"]),
            f"{r['accuracy']:.0%} [{r['acc_lo']:.0%}, {r['acc_hi']:.0%}]",
            fmt(r.get("mean_conf")),
            fmt(r.get("brier"), False), fmt(r.get("ece"), False), fmt(r.get("aurc"), False),
            f"{r['e_aurc']:.3f} [{r['e_aurc_lo']:.3f}, {r['e_aurc_hi']:.3f}]" if has else "-",
            fmt(r.get("acc@cov50")), fmt(r.get("acc@cov80")),
            f"{fmt(r.get('cov@tau0.5'))} / {fmt(r.get('acc@tau0.5'))}" if has else "-",
            f"{fmt(r.get('cov@tau0.78'))} / {fmt(r.get('acc@tau0.78'))}" if has else "-",
        ]) + " |")
    return "\n".join(lines)


def plot(curves: dict, out: Path) -> Optional[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    models = sorted({m for m, _ in curves})
    fig, axes = plt.subplots(1, len(models), figsize=(6 * len(models), 4.5), sharey=True, squeeze=False)
    for ax, model in zip(axes[0], models):
        for (m, policy), pts in sorted(curves.items(), key=lambda kv: ORDER.index(kv[0][1])
                                       if kv[0][1] in ORDER else 99):
            if m != model:
                continue
            ax.step([0] + [c for c, _ in pts], [pts[0][1]] + [r for _, r in pts], where="post",
                    label=LABEL.get(policy, policy), color=COLOR.get(policy))
        ax.set_title(model)
        ax.set_xlabel("coverage (share of incidents answered)")
        ax.set_xlim(0, 1)
        ax.grid(alpha=0.3)
    axes[0][0].set_ylabel("risk (error rate on answered)")
    for ax in axes[0]:
        ax.legend(loc="lower right", fontsize=8)
    fig.suptitle("Risk–coverage: lower and flatter-on-the-left is better")
    fig.tight_layout()
    path = out / "risk_coverage.png"
    fig.savefig(path, dpi=140)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default=None, help="write table.md, table.csv, risk_coverage.png here")
    args = ap.parse_args()
    table, curves = summarise(load(args.paths))
    md = markdown(table)
    print(md)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "table.md").write_text(md + "\n", encoding="utf-8")
        with open(out / "table.csv", "w", newline="", encoding="utf-8") as f:
            keys = list(dict.fromkeys(k for r in table for k in r))
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(table)
        p = plot(curves, out)
        print(f"\nwrote {out}/table.md, table.csv" + (f", {p.name}" if p else " (no matplotlib: no plot)"))


if __name__ == "__main__":
    main()
