"""Scorer tests. Run from testbed/ops:  py -m unittest test_score"""
import json
import tempfile
import unittest
from pathlib import Path

from testbed_ops.score import load_faults, load_incidents, match


def _write(folder: Path, name: str, obj) -> None:
    text = obj if isinstance(obj, str) else json.dumps(obj)
    (folder / name).write_text(text, encoding="utf-8")


class MatchTest(unittest.TestCase):
    def test_incident_during_a_fault_is_scored_and_a_second_one_is_a_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            key = root / "ground_truth.jsonl"
            key.write_text(
                "\n".join([
                    json.dumps({"ts": "2026-10-12T10:00:00+00:00", "event": "inject",
                                "fault_id": "a1", "fault": "bad_release_worker",
                                "service": "worker", "correct_remedy": "rollback_release:worker"}),
                    json.dumps({"ts": "2026-10-12T10:04:00+00:00", "event": "resolved",
                                "fault_id": "a1", "minutes": 4.0}),
                    json.dumps({"ts": "2026-10-12T10:04:00+00:00", "event": "end",
                                "fault_id": "a1", "ended_by": "fixed_before_injector_heal"}),
                ]),
                encoding="utf-8",
            )
            inc = root / "incidents"
            inc.mkdir()
            _write(inc, "first.json", {"id": "first", "opened": "2026-10-12T10:01:00+00:00"})
            _write(inc, "again.json", {"id": "again", "opened": "2026-10-12T10:02:00+00:00"})
            _write(inc, "later.json", {"id": "later", "opened": "2026-10-12T11:00:00+00:00"})

            m = match(load_faults(key), load_incidents(inc))

        self.assertEqual(list(m.scored), ["first"])
        self.assertEqual(m.scored["first"].fault, "bad_release_worker")
        self.assertEqual(m.scored["first"].resolved_minutes, 4.0)
        self.assertEqual(m.duplicates, ["again"])
        self.assertEqual(m.natural, ["later"])
        self.assertEqual(m.missed, [])

    def test_a_fault_with_no_incident_is_missed(self):
        with tempfile.TemporaryDirectory() as d:
            key = Path(d) / "ground_truth.jsonl"
            key.write_text(json.dumps(
                {"ts": "2026-10-12T10:00:00+00:00", "event": "inject", "fault_id": "b1",
                 "fault": "redis_oom", "service": "redis",
                 "correct_remedy": "restore_redis_maxmemory:redis"}) + "\n", encoding="utf-8")
            m = match(load_faults(key), [])
        self.assertEqual([f.fault for f in m.missed], ["redis_oom"])


if __name__ == "__main__":
    unittest.main()
