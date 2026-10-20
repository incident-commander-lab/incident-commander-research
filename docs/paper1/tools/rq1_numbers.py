"""Generate every RQ1 number in Paper 1 from the data, as LaTeX (never type them by hand).

    python docs/paper1/tools/rq1_numbers.py            # from the repo root; writes docs/paper1/numbers_rq1.tex

Inputs: the final RQ1 snapshot (data/rq1-results/2026-10-15, checksum checked) and the per-task
calibration tables (data/rq1-eval/2026-10-19/by_task). Scoring uses ic.bench.analyze, the same
functions as the results table in the snapshot README, so the paper and the repo cannot disagree.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ic.bench.analyze import actions_of, success, wilson  # noqa: E402
from ic.bench.replay import ground_truth, parse_submission  # noqa: E402

SNAP = ROOT / "data/rq1-results/2026-10-15"
EVAL = ROOT / "data/rq1-eval/2026-10-19/by_task"
OUT = ROOT / "docs/paper1/numbers_rq1.tex"

POLICIES = ["react", "llm_choose", "voi", "voi2", "random", "exhaustive"]
NAME = {"react": "Plain LLM (ReAct)", "llm_choose": "LLM chooses checks", "voi": "Rule v1",
        "voi2": "Rule v2", "random": "Random checks", "exhaustive": "Every check"}
MODELS = {"qwen3:8b": "Eight", "qwen3:14b": "Fourteen"}
GROUPS = {"all": None, "det": {"detection"}, "la": {"localization", "analysis"}}


def load() -> list[dict]:
    f = SNAP / "full_rq1.jsonl.gz"
    want = next(l.split()[0] for l in (SNAP / "SHA256SUMS").read_text().splitlines() if l.endswith(f.name))
    if hashlib.sha256(f.read_bytes()).hexdigest() != want:
        sys.exit(f"{f} does not match SHA256SUMS")
    return [json.loads(l) for l in gzip.open(f, "rt", encoding="utf-8")]


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def pct(x: float) -> str:
    return f"{round(100 * x)}\\%"


def ci(lo: float, hi: float) -> str:
    return f"[{round(100 * lo)}, {round(100 * hi)}]"


def tok(x: float) -> str:
    return f"{x / 1000:.1f}k" if x < 10_000 else f"{round(x / 1000)}k"


def pval(p: float) -> str:
    """With its relation, for use as $p\\pX$ in math mode: '<0.001' or '=0.38'."""
    return "<0.001" if p < 0.001 else f"={p:.3f}" if p < 0.01 else f"={p:.2f}"


def main():
    rows = load()
    by = defaultdict(dict)                     # (model, policy) -> problem -> run
    for r in rows:
        by[(r["model"], r["policy"])][r["problem"]] = r
    task_of = {r["problem"]: r["task"] for r in rows}
    gt = ground_truth(rows)

    macros, lines = {}, []

    def m(name, value):
        macros[name] = value

    n_task = defaultdict(int)
    for p, t in task_of.items():
        n_task[t] += 1
    det_fault = sum(1 for p, t in task_of.items() if t == "detection" and gt[p].get("detection") == "Yes")
    m("nProblems", str(len(task_of)))
    m("nDetection", str(n_task["detection"]))
    m("nLocalization", str(n_task["localization"]))
    m("nAnalysis", str(n_task["analysis"]))
    m("nLocAna", str(n_task["localization"] + n_task["analysis"]))
    m("nDetFault", str(det_fault))
    m("nRuns", str(len(rows)))
    m("accAlwaysYes", pct(det_fault / len(task_of)))
    m("accAlwaysYesDet", pct(det_fault / n_task["detection"]))

    def acc(model, pol, group):
        runs = [r for p, r in by[(model, pol)].items() if GROUPS[group] is None or task_of[p] in GROUPS[group]]
        k, n = sum(success(r) for r in runs), len(runs)
        return k, n

    # accuracy table rows (per model, per condition, per task group)
    table = []
    for model, tag in MODELS.items():
        for pol in POLICIES:
            if not by.get((model, pol)):
                continue
            cells = []
            for g in ("all", "det", "la"):
                k, n = acc(model, pol, g)
                lo, hi = wilson(k, n)
                cells.append(f"{pct(k / n)} {{\\scriptsize {ci(lo, hi)}}}")
            runs = list(by[(model, pol)].values())
            checks = sum(len(actions_of(r)) for r in runs) / len(runs)
            tokens = sum((r.get("llm_usage") or {}).get("input_tokens", 0)
                         + (r.get("llm_usage") or {}).get("output_tokens", 0) for r in runs) / len(runs)
            table.append(f"{model.split(':')[1].upper()} & {NAME[pol]} & " + " & ".join(cells)
                         + f" & {checks:.1f} & {tok(tokens)} \\\\")
            key = {"react": "React", "llm_choose": "Choose", "voi": "VOne", "voi2": "Ours",
                   "random": "Random", "exhaustive": "Every"}[pol]
            k, n = acc(model, pol, "all")
            m(f"acc{key}{tag}", pct(k / n))
            k, n = acc(model, pol, "la")
            m(f"accLA{key}{tag}", pct(k / n))
            k, n = acc(model, pol, "det")
            m(f"accDet{key}{tag}", pct(k / n))
            m(f"tok{key}{tag}", tok(tokens))
            m(f"chk{key}{tag}", f"{checks:.1f}")
        table.append("\\midrule")
    table.pop()

    # paired comparisons of rule v2 against each other condition (same model, same problems)
    paired = []
    for model, tag in MODELS.items():
        for pol in POLICIES:
            if pol == "voi2" or not by.get((model, pol)):
                continue
            cells = []
            for g in ("all", "det", "la"):
                probs = [p for p in by[(model, "voi2")] if p in by[(model, pol)]
                         and (GROUPS[g] is None or task_of[p] in GROUPS[g])]
                b = sum(success(by[(model, "voi2")][p]) and not success(by[(model, pol)][p]) for p in probs)
                c = sum(success(by[(model, pol)][p]) and not success(by[(model, "voi2")][p]) for p in probs)
                pv = mcnemar_exact(b, c)
                cells.append(f"{b}/{c}, $p{pval(pv)}$")
                key = {"react": "React", "llm_choose": "Choose", "voi": "VOne",
                       "random": "Random", "exhaustive": "Every"}[pol]
                m(f"p{key}{tag}{ {'all': '', 'det': 'Det', 'la': 'LA'}[g] }", pval(pv))
            paired.append(f"{model.split(':')[1].upper()} & Rule v2 vs {NAME[pol]} & " + " & ".join(cells) + " \\\\")
        paired.append("\\midrule")
    paired.pop()

    # why the plain LLM agent fails detection: "No" answers (8B) and the step budget (14B)
    for model, tag in MODELS.items():
        det = [r for p, r in by[(model, "react")].items() if task_of[p] == "detection"]
        m(f"reactDetNo{tag}", str(sum(parse_submission(r.get("submission"), "detection") == "No" for r in det)))
        m(f"reactDetBudget{tag}", str(sum(r.get("stop_reason") == "budget" for r in det)))

    # the 14B LLM chooser's answers without any check, and how many were correct detection "Yes"
    zero = [(p, r) for p, r in by[("qwen3:14b", "llm_choose")].items() if not actions_of(r)]
    zero_ok = [(p, r) for p, r in zero if success(r)]
    m("chooseZeroChkFourteen", str(len(zero)))
    m("chooseZeroOkFourteen", str(len(zero_ok)))
    m("chooseZeroOkYesFourteen", str(sum(task_of[p] == "detection"
                                         and parse_submission(r.get("submission"), "detection") == "Yes"
                                         for p, r in zero_ok)))

    # token ratios quoted in the text
    t = lambda key, tag: float(macros[f"tok{key}{tag}"].rstrip("k")) * 1000
    for tag in MODELS.values():
        m(f"tokRatioReact{tag}", f"{t('React', tag) / t('Ours', tag):.0f}")
        m(f"tokRatioChoose{tag}", f"{t('Choose', tag) / t('Ours', tag):.1f}")
    m("chkRatioEveryEight", f"{float(macros['chkEveryEight']) / float(macros['chkOursEight']):.1f}")

    # per-task calibration (PR #6)
    calib = []
    for g, label in (("detection", "Detection"), ("loc_analysis", "Loc.\\,+\\,analysis")):
        with open(EVAL / g / "calibration" / "table.csv", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["policy"] not in ("llm_choose", "voi", "voi2"):
                    continue
                calib.append(f"{label} & {row['model'].split(':')[1].upper()} & {NAME[row['policy']]} & "
                             f"{pct(float(row['accuracy']))} & {pct(float(row['mean_conf']))} & "
                             f"{float(row['ece']):.2f} & {float(row['brier']):.2f} & "
                             f"{pct(float(row['cov@tau0.78']))} / {pct(float(row['acc@tau0.78']))} \\\\")
                if row["policy"] == "voi2":
                    tag = MODELS[row["model"]]
                    gk = "Det" if g == "detection" else "LA"
                    m(f"ece{gk}Ours{tag}", f"{float(row['ece']):.2f}")
                    m(f"conf{gk}Ours{tag}", pct(float(row["mean_conf"])))
                    m(f"keep{gk}Ours{tag}", pct(float(row["cov@tau0.78"])))
                    m(f"keepAcc{gk}Ours{tag}", pct(float(row["acc@tau0.78"])))
        calib.append("\\midrule")
    calib.pop()

    out = ["% GENERATED by docs/paper1/tools/rq1_numbers.py from data/rq1-results/2026-10-15 and",
           "% data/rq1-eval/2026-10-19. Do not edit; rerun the script.", ""]
    out += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in macros.items()]
    out += ["", "\\newcommand{\\rqOneAccuracyRows}{%"] + table + ["}", ""]
    out += ["\\newcommand{\\rqOnePairedRows}{%"] + paired + ["}", ""]
    out += ["\\newcommand{\\rqOneCalibRows}{%"] + calib + ["}"]
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(macros)} macros")


if __name__ == "__main__":
    main()
