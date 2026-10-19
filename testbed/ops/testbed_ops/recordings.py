"""Build and check the traffic recordings that scored sessions replay (RQ3_PROTOCOL.md, "Seeds").

The collector records live traffic to hourly files, recordings/wikipedia-YYYYMMDDHH.ndjson
(one {"recv_ts", "data"} per line). A scored session needs one continuous file that lasts
longer than the session, so this tool finds qualifying windows and joins hourly files.

    cd testbed/ops
    python -m testbed_ops.recordings scan  ../recordings              # every window that qualifies
    python -m testbed_ops.recordings make  ../recordings 2026101912 seed101.ndjson
    python -m testbed_ops.recordings check ../recordings/seed101.ndjson

Selection rule (pre-registered): a window qualifies when it is at least MIN_HOURS long with no
gap between consecutive events above MAX_GAP_S. Seeds 101-106 take the first qualifying window
starting at 00, 04, 08, 12, 16 and 20 UTC respectively, so the six cover the daily load cycle.
Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MIN_HOURS = 4.5
MAX_GAP_S = 60.0
SEED_START_HOURS = {101: 0, 102: 4, 103: 8, 104: 12, 105: 16, 106: 20}
HOURLY = re.compile(r"wikipedia-(\d{10})\.ndjson$")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def hourly_files(folder: Path) -> dict[datetime, Path]:
    out = {}
    for p in Path(folder).iterdir():
        m = HOURLY.search(p.name)
        if m:
            out[datetime.strptime(m.group(1), "%Y%m%d%H").replace(tzinfo=timezone.utc)] = p
    return dict(sorted(out.items()))


def _times(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield _ts(json.loads(line)["recv_ts"])


def check(path: Path) -> dict:
    """Duration, events, mean rate, largest gap and SHA-256 of one recording."""
    first = last = None
    n, max_gap, unordered = 0, 0.0, 0
    for t in _times(path):
        if last is not None:
            gap = (t - last).total_seconds()
            if gap < 0:
                unordered += 1
            max_gap = max(max_gap, gap)
        first = first or t
        last = t
        n += 1
    hours = (last - first).total_seconds() / 3600 if n > 1 else 0.0
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"file": path.name, "first": first.isoformat() if first else None, "hours": round(hours, 3),
            "events": n, "events_per_s": round(n / (hours * 3600), 2) if hours else None,
            "max_gap_s": round(max_gap, 1), "out_of_order": unordered, "sha256": sha,
            "qualifies": hours >= MIN_HOURS and max_gap <= MAX_GAP_S and unordered == 0}


def windows(folder: Path) -> list[dict]:
    """Each start hour from which consecutive hourly files reach MIN_HOURS with no long gap."""
    files = hourly_files(folder)
    need = int(-(-MIN_HOURS // 1)) + 1   # whole hours needed to cover MIN_HOURS from any start
    out = []
    for start in files:
        hours = [start + timedelta(hours=i) for i in range(need)]
        if not all(h in files for h in hours):
            continue
        last, max_gap, n = None, 0.0, 0
        for h in hours:
            for t in _times(files[h]):
                if last is not None:
                    max_gap = max(max_gap, (t - last).total_seconds())
                last = t
                n += 1
        out.append({"start": start.strftime("%Y%m%d%H"), "hour_utc": start.hour, "files": need,
                    "events": n, "max_gap_s": round(max_gap, 1), "qualifies": max_gap <= MAX_GAP_S})
    return out


def assign(found: list[dict]) -> dict[int, str]:
    """The pre-registered rule: for each seed, the first qualifying window at its start hour."""
    out = {}
    for seed, hour in SEED_START_HOURS.items():
        w = next((w for w in found if w["qualifies"] and w["hour_utc"] == hour), None)
        if w:
            out[seed] = w["start"]
    return out


def make(folder: Path, start: str, out_name: str) -> dict:
    files = hourly_files(folder)
    t0 = datetime.strptime(start, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    need = int(-(-MIN_HOURS // 1)) + 1
    parts = [files.get(t0 + timedelta(hours=i)) for i in range(need)]
    if not all(parts):
        raise SystemExit(f"missing hourly files for a {need}-hour window from {start}")
    out = Path(folder) / out_name
    with out.open("w", encoding="utf-8") as f:
        for p in parts:
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    f.write(line + "\n")
    res = check(out)
    if not res["qualifies"]:
        out.unlink()
        raise SystemExit(f"window {start} does not qualify: {res}")
    return res


def main(argv: list[str]):
    if len(argv) == 2 and argv[0] == "scan":
        found = windows(Path(argv[1]))
        for w in found:
            print(f"{w['start']}  {w['events']:>8} events  max gap {w['max_gap_s']:>6}s  "
                  f"{'qualifies' if w['qualifies'] else 'GAP'}")
        print("\nassignment by the pre-registered rule:")
        chosen = assign(found)
        for seed, hour in SEED_START_HOURS.items():
            print(f"  seed {seed} (start {hour:02d} UTC): {chosen.get(seed, 'NONE YET: record more')}")
    elif len(argv) == 4 and argv[0] == "make":
        print(json.dumps(make(Path(argv[1]), argv[2], argv[3]), indent=1))
    elif len(argv) == 2 and argv[0] == "check":
        print(json.dumps(check(Path(argv[1])), indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
