"""Score a live test-bed session against the sealed answer key (docs/paper/RQ3_PROTOCOL.md).

After the session, copy the answer key out of the sealed volume, then score:

    docker compose exec chaos cat /sealed/ground_truth.jsonl > ground_truth.jsonl
    cd testbed/ops && python -m testbed_ops.score ../ground_truth.jsonl ../incidents

Standard library only, so it runs on the host without the test bed's dependencies.
"""
from __future__ import annotations

import json
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


def main(argv: list[str]):
    if len(argv) != 2:
        sys.exit(__doc__)
    faults = load_faults(Path(argv[0]))
    incidents = load_incidents(Path(argv[1]))
    print(report(match(faults, incidents)))


if __name__ == "__main__":
    main(sys.argv[1:])
