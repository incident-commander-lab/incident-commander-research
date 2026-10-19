"""Tests for the RQ3 analysis plan in the scorer. Run from testbed/ops:  py -m unittest test_analysis"""
import json
import tempfile
import unittest
from pathlib import Path

from testbed_ops.score import (DELTA, Z95, analyze, holm, load_schedule_events, load_session,
                               newcombe_independent, newcombe_paired, p_above)


class NewcombeIndependentTest(unittest.TestCase):
    # Newcombe (1998), Statistics in Medicine 17:873-890, Table II, method 10
    def test_published_example_56_of_70_vs_48_of_80(self):
        lo, hi = newcombe_independent(56, 70, 48, 80)
        self.assertAlmostEqual(lo, 0.0524, places=4)
        self.assertAlmostEqual(hi, 0.3339, places=4)

    def test_published_example_9_of_10_vs_3_of_10(self):
        lo, hi = newcombe_independent(9, 10, 3, 10)
        self.assertAlmostEqual(lo, 0.1705, places=4)
        self.assertAlmostEqual(hi, 0.8090, places=4)

    def test_swapping_the_groups_mirrors_the_interval(self):
        lo, hi = newcombe_independent(7, 20, 12, 25)
        lo2, hi2 = newcombe_independent(12, 25, 7, 20)
        self.assertAlmostEqual(lo, -hi2)
        self.assertAlmostEqual(hi, -lo2)

    def test_empty_group_gives_no_interval(self):
        self.assertIsNone(newcombe_independent(0, 0, 3, 10))


class NewcombePairedTest(unittest.TestCase):
    def test_uncorrelated_table_reduces_to_the_independent_interval(self):
        # a*d == b*c, so phi = 0 and the paired interval has nothing to correct
        both, only_a, only_b, neither = 4, 2, 6, 3
        n = both + only_a + only_b + neither
        self.assertAlmostEqual(both * neither, only_a * only_b)
        paired = newcombe_paired(both, only_a, only_b, neither)
        indep = newcombe_independent(both + only_a, n, both + only_b, n)
        self.assertAlmostEqual(paired[0], indep[0])
        self.assertAlmostEqual(paired[1], indep[1])

    def test_positive_correlation_narrows_the_interval(self):
        # same margins, but the two setups mostly agree: pairing must help
        corr = newcombe_paired(20, 5, 2, 27)
        indep = newcombe_independent(25, 54, 22, 54)
        self.assertLess(corr[1] - corr[0], indep[1] - indep[0])

    def test_interval_contains_the_estimate_and_stays_in_range(self):
        for table in [(0, 0, 0, 54), (54, 0, 0, 0), (0, 54, 0, 0), (10, 8, 3, 33), (1, 0, 9, 44)]:
            lo, hi = newcombe_paired(*table)
            n = sum(table)
            est = (table[1] - table[2]) / n
            self.assertTrue(-1.0 <= lo <= est + 1e-12 and est - 1e-12 <= hi <= 1.0, table)

    def test_swapping_a_and_b_mirrors_the_interval(self):
        lo, hi = newcombe_paired(10, 8, 3, 33)
        lo2, hi2 = newcombe_paired(10, 3, 8, 33)
        self.assertAlmostEqual(lo, -hi2)
        self.assertAlmostEqual(hi, -lo2)


class InversionTest(unittest.TestCase):
    def test_p_value_agrees_with_the_95_interval(self):
        # the lower limit of the 95% interval is above the bound exactly when p < 0.025
        for k1, k2 in [(40, 30), (30, 30), (45, 20), (28, 30)]:
            ci = newcombe_independent(k1, 54, k2, 54)
            p = p_above(lambda z: newcombe_independent(k1, 54, k2, 54, z=z), -DELTA)
            self.assertEqual(ci[0] > -DELTA, p < 0.025, (k1, k2, ci, p))

    def test_at_the_boundary_the_level_matches_the_interval(self):
        f = lambda z: newcombe_independent(40, 54, 30, 54, z=z)
        lo = f(Z95)[0]
        self.assertAlmostEqual(p_above(f, lo), 0.025, places=6)

    def test_estimate_at_or_below_the_bound_gives_1(self):
        self.assertEqual(p_above(lambda z: newcombe_independent(10, 54, 30, 54, z=z), 0.0), 1.0)


class HolmTest(unittest.TestCase):
    def test_step_down_adjustment(self):
        # sorted 0.01, 0.02, 0.04 -> 3*0.01, max(0.03, 2*0.02), max(0.04, 1*0.04)
        adj = holm({"x": 0.04, "y": 0.01, "z": 0.02})
        self.assertAlmostEqual(adj["y"], 0.03)
        self.assertAlmostEqual(adj["z"], 0.04)
        self.assertAlmostEqual(adj["x"], 0.04)

    def test_adjusted_values_are_capped_and_monotone(self):
        adj = holm({"x": 0.6, "y": 0.5})
        self.assertEqual(adj["y"], 1.0)
        self.assertEqual(adj["x"], 1.0)


# ------------------------------------------------------------------ end to end on synthetic sessions
FAULTS = [("bad_release_worker", "worker", "rollback_release:worker"),
          ("redis_oom", "redis", "restore_redis_maxmemory:redis"),
          ("upstream_cut", "collector", "none")]


def _session(root: Path, kind: str, seed: int, outcomes: list[str], **header) -> Path:
    """One session folder. outcomes per fault: 'fix' (correct autonomous fix), 'harm' (wrong
    autonomous action), 'escalate' (escalated with a correct proposal), 'miss' (no incident)."""
    folder = root / f"{kind}-seed{seed}"
    folder.mkdir()
    events = [{"ts": "2026-10-20T09:00:00+00:00", "event": "schedule", "seed": seed}]
    for i, ((fault, service, remedy), out) in enumerate(zip(FAULTS, outcomes)):
        t, fid = f"2026-10-20T{10 + i:02d}:00:00+00:00", f"{kind}{seed}-{i}"
        events.append({"ts": t, "event": "inject", "fault_id": fid, "fault": fault,
                       "service": service, "correct_remedy": remedy})
        if out == "fix":
            events.append({"ts": t, "event": "resolved", "fault_id": fid, "minutes": 3.0})
        events.append({"ts": f"2026-10-20T{10 + i:02d}:15:00+00:00", "event": "end", "fault_id": fid,
                       "ended_by": "fixed_before_injector_heal" if out == "fix" else "injector_timeout"})
        if out == "miss":
            continue
        rec = {"id": fid, "opened": f"2026-10-20T{10 + i:02d}:02:00+00:00", "autonomy": "act",
               "diagnosis": {"service": service, "remedy": remedy, "p_remedy": 0.9}}
        if out == "fix":
            rec |= {"action": {"remedy": remedy}, "verification": {"recovered": True}}
        elif out == "harm":
            rec |= {"action": {"remedy": "restart:api"}, "verification": {"recovered": False}}
        (folder / f"{fid}.json").write_text(json.dumps(rec), encoding="utf-8")
    (folder / "ground_truth.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    head = {"session": folder.name, "AGENT_KIND": kind, "CHAOS_SEED": seed, "CHAOS_SLOTS": 3,
            "git_commit": "abc", "uncommitted_changes": False, "AGENT_MODEL": "qwen3:8b",
            "model_digest": "sha256:1", "llm_temperature": 0.3, "AUTONOMY": "act",
            "CHAOS_SLOT_MIN": 25, "FAULT_TIMEOUT_MIN": 15, "VERIFY_SECONDS": 120,
            "COST_ACT": 2, "COST_ESCALATE": 15, "COST_HARM": 60, "REPLAY_FILE": f"rec-{seed}.ndjson"}
    head.update(header)
    (folder / "header.json").write_text(json.dumps(head), encoding="utf-8")
    return folder


class AnalyzeTest(unittest.TestCase):
    def test_pairs_by_seed_and_reports_every_hypothesis(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dirs = [_session(root, "rule", 101, ["fix", "fix", "escalate"]),
                    _session(root, "rule", 102, ["fix", "escalate", "escalate"]),
                    _session(root, "llm", 101, ["fix", "harm", "harm"]),
                    _session(root, "llm", 102, ["harm", "fix", "harm"])]
            res = analyze([load_session(p) for p in dirs])
        self.assertEqual(res["paired_faults"], 6)
        self.assertEqual(res["seeds"], [101, 102])
        self.assertEqual((res["H3a"]["a"], res["H3a"]["b"]), (0, 4))
        self.assertEqual((res["H3a"]["only_a"], res["H3a"]["only_b"]), (0, 4))
        self.assertAlmostEqual(res["H3a"]["p"], 0.125)   # 4 discordant pairs, all one way: 2 * 1/16
        self.assertFalse(res["H3a"]["supported"])        # too few faults to reach 0.05
        self.assertEqual((res["H3b"]["a"], res["H3b"]["b"]), (3, 2))
        # rule escalated 3 times: two on upstream_cut (proposed `none`: needed) and one on
        # redis_oom with the correct fix (unneeded)
        self.assertEqual((res["H3c"]["a"], res["H3c"]["na"]), (2, 3))
        for h in ("H3b", "H3c", "H3d"):
            self.assertGreaterEqual(res[h]["p_holm"], res[h]["p"])
        self.assertIn(res["H3b"]["verdict"], ("non-inferior", "inconclusive", "worse by more than the margin"))
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["calibration"]["rule"]["fix_correct"]["n"], 6)

    def test_refuses_sessions_that_cannot_be_paired(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dirs = [_session(root, "rule", 101, ["fix"] * 3), _session(root, "llm", 102, ["fix"] * 3)]
            with self.assertRaisesRegex(ValueError, "seeds differ"):
                analyze([load_session(p) for p in dirs])

    def test_refuses_different_replay_files_for_the_same_seed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dirs = [_session(root, "rule", 101, ["fix"] * 3),
                    _session(root, "llm", 101, ["fix"] * 3, REPLAY_FILE="other.ndjson")]
            with self.assertRaisesRegex(ValueError, "replay files differ"):
                analyze([load_session(p) for p in dirs])

    def test_warns_about_frozen_settings_and_dirty_runs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dirs = [_session(root, "rule", 101, ["fix"] * 3, uncommitted_changes=True),
                    _session(root, "llm", 101, ["fix"] * 3, model_digest="sha256:2", CHAOS_SLOTS=4)]
            warnings = analyze([load_session(p) for p in dirs])["warnings"]
        self.assertTrue(any("model_digest differs" in w for w in warnings))
        self.assertTrue(any("uncommitted changes" in w for w in warnings))
        self.assertTrue(any("not whole rounds" in w for w in warnings))


class ScheduleEventsTest(unittest.TestCase):
    def test_counts_slot_delays_and_failed_injections(self):
        with tempfile.TemporaryDirectory() as d:
            key = Path(d) / "ground_truth.jsonl"
            key.write_text("\n".join(json.dumps(e) for e in [
                {"event": "schedule"}, {"event": "slot_delayed", "fault": "db_lock"},
                {"event": "slot_delayed", "fault": "db_lock"}, {"event": "inject_failed", "fault": "api_down"},
                {"event": "inject", "fault_id": "x"}]), encoding="utf-8")
            self.assertEqual(load_schedule_events(key),
                             {"slot_delayed": 2, "inject_failed": 1, "slot_skipped_restart": 0})


if __name__ == "__main__":
    unittest.main()
