"""Scorer tests. Run from testbed/ops:  py -m unittest test_score"""
import json
import tempfile
import unittest
from pathlib import Path

from testbed_ops.score import (Fault, brier, load_faults, load_incidents,
                               match, reliability, session_metrics, verdict)
from datetime import datetime, timezone


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


def _fault(**kw) -> Fault:
    base = dict(fault_id="a1", fault="bad_release_worker", service="worker",
                correct_remedy="rollback_release:worker",
                injected=datetime(2026, 10, 12, tzinfo=timezone.utc), resolved_minutes=4.0)
    base.update(kw)
    return Fault(**base)


def _rec(**kw) -> dict:
    rec = {"id": "i1", "autonomy": "act",
           "diagnosis": {"service": "worker", "remedy": "rollback_release:worker",
                         "p_remedy": 0.97, "decision": "act"},
           "action": {"remedy": "rollback_release:worker"},
           "verification": {"recovered": True}}
    rec.update(kw)
    return rec


class VerdictTest(unittest.TestCase):
    def test_a_correct_fix_that_holds_is_an_autonomous_resolution(self):
        v = verdict(_rec(), _fault())
        self.assertTrue(v["diagnosis_correct"])
        self.assertTrue(v["fix_correct"])
        self.assertTrue(v["resolved_autonomously"])
        self.assertFalse(v["harmful"])

    def test_the_wrong_action_is_harmful(self):
        v = verdict(_rec(action={"remedy": "restart:api"}), _fault())
        self.assertTrue(v["harmful"])
        self.assertFalse(v["resolved_autonomously"])

    def test_escalating_a_correct_fix_was_not_needed(self):
        rec = _rec(diagnosis={"service": "worker", "remedy": "rollback_release:worker", "decision": "escalate"})
        del rec["action"]
        del rec["verification"]
        v = verdict(rec, _fault())
        self.assertTrue(v["escalation_unneeded"])
        self.assertFalse(v["escalation_needed"])
        self.assertFalse(v["harmful"])

    def test_escalating_a_wrong_fix_was_needed(self):
        rec = _rec(diagnosis={"service": "redis", "remedy": "none", "decision": "escalate"})
        del rec["action"]
        del rec["verification"]
        v = verdict(rec, _fault())
        self.assertTrue(v["escalation_needed"])
        self.assertFalse(v["diagnosis_correct"])

    def test_acting_on_a_natural_incident_is_harmful(self):
        self.assertTrue(verdict(_rec(), None)["harmful"])
        rec = _rec()
        del rec["action"]
        del rec["verification"]
        self.assertFalse(verdict(rec, None)["harmful"])

    def test_a_human_approval_is_not_autonomous(self):
        v = verdict(_rec(approved_by_human="2026-10-12T10:03:00+00:00"), _fault())
        self.assertFalse(v["resolved_autonomously"])


class SessionTest(unittest.TestCase):
    def test_a_missed_fault_counts_against_autonomous_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            key = root / "ground_truth.jsonl"
            key.write_text("\n".join([
                json.dumps({"ts": "2026-10-12T10:00:00+00:00", "event": "inject", "fault_id": "a1",
                            "fault": "bad_release_worker", "service": "worker",
                            "correct_remedy": "rollback_release:worker"}),
                json.dumps({"ts": "2026-10-12T10:04:00+00:00", "event": "resolved",
                            "fault_id": "a1", "minutes": 4.0}),
                json.dumps({"ts": "2026-10-12T10:04:00+00:00", "event": "end", "fault_id": "a1",
                            "ended_by": "fixed_before_injector_heal"}),
                json.dumps({"ts": "2026-10-12T11:00:00+00:00", "event": "inject", "fault_id": "b1",
                            "fault": "redis_oom", "service": "redis",
                            "correct_remedy": "restore_redis_maxmemory:redis"}),
                json.dumps({"ts": "2026-10-12T11:20:00+00:00", "event": "end", "fault_id": "b1",
                            "ended_by": "timeout"}),
            ]), encoding="utf-8")
            inc = root / "incidents"
            inc.mkdir()
            (inc / "ok.json").write_text(json.dumps(
                _rec(id="ok", opened="2026-10-12T10:01:00+00:00",
                     llm_usage={"input_tokens": 100, "output_tokens": 20, "calls": 1},
                     investigation={"checks_paid": 3}, seconds=30)), encoding="utf-8")
            incidents = load_incidents(inc)
            m = match(load_faults(key), incidents)
        metrics = session_metrics(incidents, m)
        self.assertEqual(metrics["seen"]["autonomous_resolution"]["k"], 1)
        self.assertEqual(metrics["seen"]["autonomous_resolution"]["n"], 1)
        self.assertEqual(metrics["unseen"]["missed_faults"]["k"], 1)
        self.assertEqual(metrics["unseen"]["autonomous_resolution"]["k"], 0)
        self.assertEqual(metrics["unseen"]["autonomous_resolution"]["n"], 1)
        self.assertAlmostEqual(metrics["seen"]["detection_delay_min"], 1.0)
        self.assertEqual(metrics["seen"]["time_to_mitigation_min"], 4.0)

    def test_confidence_is_scored_against_what_happened(self):
        self.assertEqual(brier([(1.0, True), (0.0, False)]), 0.0)
        self.assertEqual(brier([(1.0, False)]), 1.0)
        bins = reliability([(0.1, False), (0.95, True)])
        self.assertEqual(bins[0]["n"], 1)
        self.assertEqual(bins[-1]["correct"], 1)


if __name__ == "__main__":
    unittest.main()
