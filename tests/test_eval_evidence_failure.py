from ic.eval.evidence_failure import evaluate, rebuild

H = {"H1": {"service": "a", "fault_type": "Code Defect", "system_level": "Application", "prior": 0.5,
            "predictions": {"pod_health:a": "unhealthy"}},
     "H2": {"service": "b", "fault_type": "Code Defect", "system_level": "Application", "prior": 0.5,
            "predictions": {"pod_health:b": "unhealthy"}},
     "H0": {"service": None, "fault_type": "None", "system_level": "None", "prior": 0.1, "predictions": {}}}
TRACE = [{"probe": "pod_health:a", "outcome": "unhealthy"},
         {"probe": "pod_health:b", "outcome": "healthy"}]


def run(policy="voi", submission='submit(["a"])', success=True):
    return {"policy": policy, "model": "m", "task": "localization", "problem": "p", "lambda": 0.02,
            "hypotheses": H, "trace": TRACE, "submission": submission,
            "aiopslab_results": {"success": success}}


def test_masking_spends_the_step_but_learns_nothing():
    full, none = rebuild(run(), set()), rebuild(run(), {0, 1})
    assert full.leader().id == "H1" and full.posteriors["H1"] > 4 * full.posteriors["H2"]
    assert none.paid_steps() == full.paid_steps() == 2
    assert abs(none.posteriors["H1"] - none.posteriors["H2"]) < 1e-12   # back to the prior


def test_unmasked_replay_reproduces_the_saved_answer():
    row0, = evaluate([run()], rates=(0.0,), seeds=1)
    assert row0["same_as_saved"] == 1.0 and row0["accuracy"] == 1.0


def test_masks_are_reproducible():
    a = evaluate([run()], rates=(0.5,), seeds=4)
    b = evaluate([run()], rates=(0.5,), seeds=4)
    assert a == b


def test_hiding_one_probe_type_hides_only_that_type():
    from ic.eval.evidence_failure import by_probe_type
    rows = by_probe_type([run()])
    by = {r["hidden"]: r for r in rows}
    assert set(by) == {"(none)", "pod_health"}
    assert by["(none)"]["runs_affected"] == 0 and by["pod_health"]["runs_affected"] == 1
    assert by["(none)"]["accuracy"] == 1.0
