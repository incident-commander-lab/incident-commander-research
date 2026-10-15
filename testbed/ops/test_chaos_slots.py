"""The scored-session schedule: same seed -> same faults at the same times; balanced rounds."""
import pytest

chaos = pytest.importorskip("testbed_ops.chaos")   # needs the test bed's Python deps (docker, psycopg, redis)


def test_same_seed_same_schedule_and_balanced():
    faults = list(chaos.FAULTS)
    a = chaos.slot_plan(101, 2 * len(faults), faults, 25, 3)
    assert a == chaos.slot_plan(101, 2 * len(faults), faults, 25, 3)
    assert a != chaos.slot_plan(102, 2 * len(faults), faults, 25, 3)
    first, second = [f for _, f in a[:len(faults)]], [f for _, f in a[len(faults):]]
    assert sorted(first) == sorted(faults) and sorted(second) == sorted(faults)
    starts = [t for t, _ in a]
    assert all(b - x >= 25 - 3 for x, b in zip(starts, starts[1:]))   # faults never overlap (slot > timeout)
