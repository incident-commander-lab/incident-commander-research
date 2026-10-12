"""Score a live test-bed session against the sealed answer key (docs/paper/RQ3_PROTOCOL.md).

After the session, copy the answer key out of the sealed volume, then score:

    docker compose exec chaos cat /sealed/ground_truth.jsonl > ground_truth.jsonl
    cd testbed/ops && python -m testbed_ops.score ../ground_truth.jsonl ../incidents

Standard library only, so it runs on the host without the test bed's dependencies.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class Fault:
    fault_id: str
    fault: str
    service: str
    correct_remedy: str
    injected: datetime
    ended: Optional[datetime] = None      # None: still active when the session stopped
    ended_by: Optional[str] = None
    resolved_minutes: Optional[float] = None

    def active_at(self, t: datetime) -> bool:
        return self.injected <= t and (self.ended is None or t <= self.ended)


@dataclass
class Match:
    faults: list[Fault]
    scored: dict[str, Fault] = field(default_factory=dict)   # incident id -> its fault
    duplicates: list[str] = field(default_factory=list)      # later incidents on an already-scored fault
    natural: list[str] = field(default_factory=list)         # incidents with no active fault
    missed: list[Fault] = field(default_factory=list)        # faults with no incident


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_faults(path: Path) -> list[Fault]:
    faults: dict[str, Fault] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        fid = e["fault_id"]
        if e["event"] == "inject":
            faults[fid] = Fault(fid, e["fault"], e["service"], e["correct_remedy"], _ts(e["ts"]))
        elif fid in faults and e["event"] == "resolved":
            faults[fid].resolved_minutes = e["minutes"]
        elif fid in faults and e["event"] == "end":
            faults[fid].ended, faults[fid].ended_by = _ts(e["ts"]), e["ended_by"]
    return sorted(faults.values(), key=lambda f: f.injected)


def load_incidents(folder: Path) -> list[dict]:
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
    return sorted(recs, key=lambda r: r["opened"])


def match(faults: list[Fault], incidents: list[dict]) -> Match:
    m = Match(faults)
    taken: set[str] = set()
    for rec in incidents:
        t = _ts(rec["opened"])
        fault = next((f for f in faults if f.active_at(t)), None)
        if fault is None:
            m.natural.append(rec["id"])
        elif fault.fault_id in taken:
            m.duplicates.append(rec["id"])
        else:
            taken.add(fault.fault_id)
            m.scored[rec["id"]] = fault
    m.missed = [f for f in faults if f.fault_id not in taken]
    return m


def verdict(rec: dict, fault: Optional[Fault]) -> dict:
    """One incident, judged by the rules in RQ3_PROTOCOL.md."""
    diag = rec.get("diagnosis") or {}
    proposed = diag.get("remedy")
    acted = "action" in rec
    held = (rec.get("verification") or {}).get("recovered") is True
    human = bool(rec.get("approved_by_human"))
    # shadow mode is told not to act, so "no action" there is not an escalation
    escalated = not acted and rec.get("autonomy") != "shadow"
    if fault is None:
        return {"diagnosis_correct": None, "fix_correct": None, "acted": acted,
                "harmful": acted, "fix_held": held if acted else None,
                "resolved_autonomously": False,
                "escalation_needed": False, "escalation_unneeded": False}
    executed = (rec.get("action") or {}).get("remedy")
    fix_correct = proposed == fault.correct_remedy
    executed_correct = acted and executed == fault.correct_remedy
    return {
        "diagnosis_correct": diag.get("service") == fault.service,
        "fix_correct": fix_correct,
        "acted": acted,
        "harmful": acted and not executed_correct,
        "fix_held": held if acted else None,
        "resolved_autonomously": (executed_correct and held and not human
                                  and fault.resolved_minutes is not None),
        "escalation_needed": escalated and not fix_correct,
        "escalation_unneeded": escalated and fix_correct,
    }


# Tuned on during the 12 Oct shakedown, so not a held-out test of rule v2.
SEEN = {"bad_release_worker", "db_lock"}


def wilson(k: int, n: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    """Wilson 95% interval for k successes out of n. None when n is 0."""
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _rate(flags: list[bool]) -> dict:
    n, k = len(flags), sum(bool(x) for x in flags)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "wilson95": wilson(k, n)}


def _median(xs: list[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _rows(incidents: list[dict], m: Match) -> list[dict]:
    by_id = {r["id"]: r for r in incidents}
    rows = []
    for iid, fault in m.scored.items():
        rec = by_id[iid]
        v = verdict(rec, fault)
        usage = rec.get("llm_usage") or {}
        rows.append({**v, "group": "seen" if fault.fault in SEEN else "unseen",
                     "delay_min": (_ts(rec["opened"]) - fault.injected).total_seconds() / 60,
                     "mitigation_min": fault.resolved_minutes,
                     "p_remedy": (rec.get("diagnosis") or {}).get("p_remedy"),
                     "tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
                     "calls": usage.get("calls") or 0,
                     "checks": (rec.get("investigation") or {}).get("checks_paid"),
                     "seconds": rec.get("seconds"),
                     "human_minutes": rec.get("human_minutes")})
    for iid in m.natural:
        rec = by_id[iid]
        v = verdict(rec, None)
        rows.append({**v, "group": "natural", "delay_min": None, "mitigation_min": None,
                     "p_remedy": (rec.get("diagnosis") or {}).get("p_remedy")})
    return rows


def _group_metrics(rows: list[dict], missed: int, injected: int) -> dict:
    resolved = sum(r["resolved_autonomously"] for r in rows)
    # autonomous resolution is out of faults injected, so a missed fault counts against it
    base = injected or len(rows)
    return {
        "incidents": len(rows),
        "missed_faults": {"k": missed, "n": injected, "wilson95": wilson(missed, injected)},
        "diagnosis_accuracy": _rate([r["diagnosis_correct"] for r in rows if r["diagnosis_correct"] is not None]),
        "fix_accuracy": _rate([r["fix_correct"] for r in rows if r["fix_correct"] is not None]),
        "autonomous_resolution": {"k": resolved, "n": base, "rate": (resolved / base) if base else None,
                                  "wilson95": wilson(resolved, base)},
        "harmful_action": _rate([r["harmful"] for r in rows]),
        "escalation_needed": _rate([r["escalation_needed"] for r in rows]),
        "escalation_unneeded": _rate([r["escalation_unneeded"] for r in rows]),
        "detection_delay_min": _median([r["delay_min"] for r in rows]),
        "time_to_mitigation_min": _median([r["mitigation_min"] for r in rows]),
        "human_wait_min": _median([r["human_minutes"] for r in rows if r.get("human_minutes") is not None]),
        "tokens_per_incident": _median([r["tokens"] for r in rows if r.get("tokens") is not None]),
        "checks_per_incident": _median([r["checks"] for r in rows if r.get("checks") is not None]),
    }


def session_metrics(incidents: list[dict], m: Match) -> dict:
    """The session table from RQ3_PROTOCOL.md, split into seen, unseen and natural."""
    rows = _rows(incidents, m)
    missed = {"seen": 0, "unseen": 0}
    injected = {"seen": 0, "unseen": 0}
    for f in m.faults:
        g = "seen" if f.fault in SEEN else "unseen"
        injected[g] += 1
    for f in m.missed:
        missed["seen" if f.fault in SEEN else "unseen"] += 1
    out = {}
    for g in ("seen", "unseen", "natural"):
        out[g] = _group_metrics([r for r in rows if r["group"] == g],
                                missed.get(g, 0), injected.get(g, 0))
    return out


def brier(pairs: list[tuple[float, bool]]) -> Optional[float]:
    """Mean squared error between a stated probability and what actually happened."""
    if not pairs:
        return None
    return sum((p - float(ok)) ** 2 for p, ok in pairs) / len(pairs)


def reliability(pairs: list[tuple[float, bool]], width: float = 0.2) -> list[dict]:
    """Incidents grouped by stated probability, with the share that was actually right."""
    bins = []
    edge = 0.0
    while edge < 1 - 1e-9:
        hi = min(1.0, edge + width)
        group = [(p, ok) for p, ok in pairs if (edge <= p < hi) or (hi == 1.0 and p == 1.0)]
        n, k = len(group), sum(ok for _, ok in group)
        bins.append({"from": edge, "to": hi, "n": n, "correct": k,
                     "rate": (k / n) if n else None})
        edge = hi
    return bins


def calibration(incidents: list[dict], m: Match) -> dict:
    """Is P(fix works) honest? Against 'fix correct' for every scored incident,
    and against 'fix held' where an action was executed."""
    rows = [r for r in _rows(incidents, m) if r["group"] != "natural" and r["p_remedy"] is not None]
    correct = [(r["p_remedy"], bool(r["fix_correct"])) for r in rows]
    held = [(r["p_remedy"], bool(r["fix_held"])) for r in rows if r["acted"]]
    return {"fix_correct": {"brier": brier(correct), "bins": reliability(correct), "n": len(correct)},
            "fix_held": {"brier": brier(held), "bins": reliability(held), "n": len(held)}}


def report(m: Match) -> str:
    lines = [f"faults injected: {len(m.faults)}   incidents scored: {len(m.scored)}   "
             f"natural: {len(m.natural)}   duplicates: {len(m.duplicates)}   missed faults: {len(m.missed)}", ""]
    lines.append(f"{'incident':24} {'fault':22} {'service':10} correct remedy")
    for iid, f in m.scored.items():
        lines.append(f"{iid:24} {f.fault:22} {f.service:10} {f.correct_remedy}")
    for iid in m.natural:
        lines.append(f"{iid:24} {'(natural)':22}")
    for f in m.missed:
        lines.append(f"{'(missed)':24} {f.fault:22} {f.service:10} {f.correct_remedy}")
    return "\n".join(lines)


def format_metrics(metrics: dict) -> str:
    lines = []
    for g, s in metrics.items():
        lines.append(f"== {g} ==")
        for name in ("diagnosis_accuracy", "fix_accuracy", "autonomous_resolution", "harmful_action",
                     "escalation_needed", "escalation_unneeded"):
            r = s[name]
            pct = "-" if r["rate"] is None else f"{r['rate']:.0%}"
            w = r["wilson95"]
            band = "" if not w else f"  (95% {w[0]:.0%}-{w[1]:.0%})"
            lines.append(f"  {name:24} {r['k']}/{r['n']}  {pct}{band}")
        lines.append(f"  {'detection delay (min)':24} {s['detection_delay_min']}")
        lines.append(f"  {'time to mitigation (min)':24} {s['time_to_mitigation_min']}")
    return "\n".join(lines)


def format_calibration(cal: dict) -> str:
    lines = []
    for name, c in cal.items():
        b = "-" if c["brier"] is None else f"{c['brier']:.3f}"
        lines.append(f"P(fix works) vs {name}: n={c['n']}  brier={b}")
        for bn in c["bins"]:
            if bn["n"]:
                lines.append(f"  {bn['from']:.1f}-{bn['to']:.1f}  {bn['correct']}/{bn['n']} actually right")
    return "\n".join(lines)


def main(argv: list[str]):
    if len(argv) != 2:
        sys.exit(__doc__)
    faults = load_faults(Path(argv[0]))
    incidents = load_incidents(Path(argv[1]))
    m = match(faults, incidents)
    print(report(m))
    print()
    print(format_metrics(session_metrics(incidents, m)))
    print()
    print(format_calibration(calibration(incidents, m)))


if __name__ == "__main__":
    main(sys.argv[1:])
