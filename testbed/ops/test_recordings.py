"""Recording tool tests. Run from testbed/ops:  py -m unittest test_recordings"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from testbed_ops.recordings import assign, check, make, windows


def _hours(folder: Path, start: datetime, n: int, every_s: float = 10.0, gap_at: datetime = None):
    """n hourly files from `start`, one event every `every_s`; optionally a 5-minute hole."""
    for h in range(n):
        t0 = start + timedelta(hours=h)
        lines, t = [], t0
        while t < t0 + timedelta(hours=1):
            if not (gap_at and gap_at <= t < gap_at + timedelta(minutes=5)):
                lines.append(json.dumps({"recv_ts": t.isoformat(), "data": "{}"}))
            t += timedelta(seconds=every_s)
        (folder / f"wikipedia-{t0:%Y%m%d%H}.ndjson").write_text("\n".join(lines) + "\n", encoding="utf-8")


START = datetime(2026, 10, 19, 0, tzinfo=timezone.utc)


class RecordingsTest(unittest.TestCase):
    def test_continuous_hours_qualify_and_join_into_one_checked_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _hours(root, START, 6)
            found = windows(root)
            self.assertEqual([w["start"] for w in found if w["qualifies"]], ["2026101900"])
            res = make(root, "2026101900", "seed101.ndjson")
            self.assertTrue(res["qualifies"])
            self.assertGreaterEqual(res["hours"], 4.5)
            self.assertEqual(len(res["sha256"]), 64)
            self.assertEqual(check(root / "seed101.ndjson")["sha256"], res["sha256"])

    def test_a_gap_disqualifies_the_window(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _hours(root, START, 6, gap_at=START + timedelta(hours=2, minutes=10))
            self.assertFalse(windows(root)[0]["qualifies"])
            with self.assertRaises(SystemExit):
                make(root, "2026101900", "bad.ndjson")
            self.assertFalse((root / "bad.ndjson").exists())

    def test_missing_hours_give_no_window(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _hours(root, START, 3)
            self.assertEqual(windows(root), [])

    def test_assignment_follows_the_start_hour_rule(self):
        found = [{"start": "2026101904", "hour_utc": 4, "qualifies": True},
                 {"start": "2026101900", "hour_utc": 0, "qualifies": False},
                 {"start": "2026102000", "hour_utc": 0, "qualifies": True},
                 {"start": "2026102004", "hour_utc": 4, "qualifies": True}]
        self.assertEqual(assign(found), {101: "2026102000", 102: "2026101904"})


if __name__ == "__main__":
    unittest.main()
