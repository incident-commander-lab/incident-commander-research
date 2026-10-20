"""cron :35 - check the previous hour's recording: events, unreadable lines, longest gap between
events (the RQ3 seed rule rejects windows with long gaps). Writes <file>.check.txt next to it."""
import datetime as dt
import json
import os
import sys

d = os.path.expanduser("~/pulse-record/recordings")
prev = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).strftime("%Y%m%d%H")
f = os.path.join(d, f"wikipedia-{prev}.ndjson")
if not os.path.exists(f) or os.path.exists(f + ".check.txt"):
    sys.exit(0)
n = bad = 0
last = None
gap = 0.0
for line in open(f, errors="replace"):
    try:
        t = dt.datetime.fromisoformat(json.loads(line)["recv_ts"].replace("Z", "+00:00")).timestamp()
        n += 1
        if last is not None:
            gap = max(gap, t - last)
        last = t
    except Exception:
        bad += 1
open(f + ".check.txt", "w").write(f"{prev} events={n} unreadable={bad} max_gap_s={gap:.1f}\n")
