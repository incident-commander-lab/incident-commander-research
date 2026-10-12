"""Evidence-failure test: how answers, confidence and abstention degrade when checks fail.

    python -m ic.eval.evidence_failure data/rq1-results/2026-10-11/full_rq1.jsonl.gz [--out DIR]

For every saved run whose answer comes from the posterior (rule v1, rule v2, random,
exhaustive), a share m of its PAID readings is hidden as if the check's tool had
failed (`Investigation.mark_unavailable`: the step is spent, nothing is learned). The
posterior is rebuilt from the remaining readings, the run's own rule re-answers, and
the answer is scored against ground truth recovered from all graded runs
(`ic.bench.replay.ground_truth`). Free discovery readings are never hidden.

Posterior only, no re-stopping: the replay cannot buy a replacement reading, so this is
the worst case for every method, and the same worst case for all of them. Each mask
rate is averaged over `--seeds` random masks per run.

Not covered: LLM-picks (its answer is the LLM's choice; re-asking needs the LLM) and
the plain LLM agent (no posterior).

Per (model, condition, mask rate): accuracy on scored runs, mean confidence, Brier, and
coverage and accuracy when abstaining below τ = 0.78. At m = 0 the share of answers
equal to the saved submission is reported as a sanity check of the replay.
"""
from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path

from ic.bench.agent import catch_alls
from ic.bench.analyze import LABEL, ORDER
from ic.bench.decide import BenchHypothesis, Investigation
from ic.bench.probes import instances
from ic.bench.replay import _services, answer, ground_truth, parse_submission, score
from ic.eval.calibration import confidence, load

POLICIES = ("voi", "voi2", "random", "exhaustive")
MASK_RATES = (0.0, 0.1, 0.2, 0.3, 0.5)
TAU = 0.78


def rule_version(run: dict) -> int:
    return run.get("rule_version") or (2 if run["policy"] == "voi2" else 1)


def rebuild(run: dict, masked: set[int]) -> Investigation:
    """The run's investigation with the readings at trace positions `masked` failed."""
    v = rule_version(run)
    probes = instances(_services(run))
    hyps = [BenchHypothesis(hid, h["service"], h["fault_type"], h["system_level"], "", h["prior"],
                            {k: o for k, o in h["predictions"].items() if k in probes})
            for hid, h in run["hypotheses"].items() if not h.get("catch_all")]
    if v >= 2:
        hyps += catch_alls(_services(run), probes)
    inv = Investigation(hyps, probes, policy="voi", lam=run.get("lambda", 0.02), rule_version=v)
    for i, s in enumerate(run["trace"]):
        if s["probe"] not in probes:
            continue
        if i in masked:
            inv.mark_unavailable(s["probe"])
        else:
            inv.observe(s["probe"], s["outcome"], free=bool(s.get("free")), eps=s.get("eps"))
    return inv


def hyp_dicts(inv: Investigation) -> dict:
    return {h.id: {"service": h.service, "system_level": h.system_level, "fault_type": h.fault_type,
                   "catch_all": h.catch_all} for h in inv.hyps.values()}


def evaluate(rows: list[dict], rates=MASK_RATES, seeds: int = 20) -> list[dict]:
    gt = ground_truth(rows)
    runs = [r for r in rows if r["policy"] in POLICIES and r.get("hypotheses") and r.get("trace")]
    acc: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))
    for r in runs:
        paid = [i for i, s in enumerate(r["trace"]) if not s.get("free")]
        key0 = (r.get("model") or "?", r["policy"])
        for m in rates:
            k = round(m * len(paid))
            for seed in range(seeds if k else 1):
                masked = set(random.Random(f"{r['problem']}|{r['policy']}|{seed}").sample(paid, k))
                inv = rebuild(r, masked)
                ans = answer(inv, r["task"], rule_version(r))
                ok = score(ans, r["task"], gt[r["problem"]])
                w = 1.0 / (seeds if k else 1)
                a = acc[key0 + (m,)]
                a["runs"] += w
                if m == 0:
                    a["same_as_saved"] += ans == parse_submission(r["submission"], r["task"])
                if ok is None:
                    a["unscored"] += w
                    continue
                c = confidence(inv.posteriors, hyp_dicts(inv), r["task"], ans) or 0.0
                a["scored"] += w
                a["correct"] += w * ok
                a["conf"] += w * c
                a["brier"] += w * (c - ok) ** 2
                if c >= TAU:
                    a["kept"] += w
                    a["kept_correct"] += w * ok
    out = []
    for (model, policy, m), a in sorted(acc.items(), key=lambda kv: (
            kv[0][0], ORDER.index(kv[0][1]) if kv[0][1] in ORDER else 99, kv[0][2])):
        s = a["scored"] or float("nan")
        out.append({"model": model, "condition": LABEL.get(policy, policy), "policy": policy,
                    "mask_rate": m, "runs": round(a["runs"]), "unscored": a["unscored"],
                    "accuracy": a["correct"] / s, "mean_conf": a["conf"] / s, "brier": a["brier"] / s,
                    f"coverage@{TAU}": a["kept"] / s,
                    f"acc@{TAU}": a["kept_correct"] / a["kept"] if a["kept"] else float("nan"),
                    "same_as_saved": a["same_as_saved"] / a["runs"] if m == 0 else None})
    return out


def markdown(table: list[dict]) -> str:
    pct = lambda v: "-" if v is None or v != v else f"{v:.0%}"
    lines = [f"| model | condition | mask | runs | unscored | accuracy | mean conf | Brier "
             f"| τ={TAU}: cov / acc | m=0 same as saved |", "|" + "---|" * 10]
    for r in table:
        lines.append(f"| {r['model']} | {r['condition']} | {r['mask_rate']:.0%} | {r['runs']} "
                     f"| {r['unscored']:.1f} | {pct(r['accuracy'])} | {pct(r['mean_conf'])} "
                     f"| {r['brier']:.3f} | {pct(r[f'coverage@{TAU}'])} / {pct(r[f'acc@{TAU}'])} "
                     f"| {pct(r['same_as_saved'])} |")
    return "\n".join(lines)


def plot(table: list[dict], out: Path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    from ic.eval.calibration import COLOR
    models = sorted({r["model"] for r in table})
    fig, axes = plt.subplots(2, len(models), figsize=(6 * len(models), 7), sharex=True, squeeze=False)
    for col, model in enumerate(models):
        for policy in POLICIES:
            rs = [r for r in table if r["model"] == model and r["policy"] == policy]
            if not rs:
                continue
            x = [r["mask_rate"] for r in rs]
            kw = {"label": LABEL.get(policy, policy), "color": COLOR.get(policy), "marker": "o"}
            axes[0][col].plot(x, [r["accuracy"] for r in rs], **kw)
            axes[1][col].plot(x, [r[f"coverage@{TAU}"] for r in rs], **kw)
        axes[0][col].set_title(model)
        axes[1][col].set_xlabel("share of paid check readings hidden")
        for ax in axes[:, col]:
            ax.set_ylim(0, 1)
            ax.grid(alpha=0.3)
            ax.legend(fontsize=8)
    axes[0][0].set_ylabel("accuracy (scored runs)")
    axes[1][0].set_ylabel(f"share confident enough to act (≥ {TAU})")
    fig.tight_layout()
    path = out / "evidence_failure.png"
    fig.savefig(path, dpi=140)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    table = evaluate(load(args.paths), seeds=args.seeds)
    md = markdown(table)
    print(md)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "evidence_failure.md").write_text(md + "\n", encoding="utf-8")
        with open(out / "evidence_failure.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0]))
            w.writeheader()
            w.writerows(table)
        p = plot(table, out)
        print(f"\nwrote {out}" + (f" ({p.name})" if p else ""))


if __name__ == "__main__":
    main()
