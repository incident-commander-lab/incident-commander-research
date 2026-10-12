from ic.eval.calibration import answer_confidence, aurc, ece, optimal_aurc, risk_coverage


def test_perfect_ranker_reaches_optimal_aurc():
    pairs = [(0.9, True), (0.8, True), (0.3, False), (0.1, False)]
    assert abs(aurc(pairs) - optimal_aurc(4, 2)) < 1e-12


def test_reversed_ranker_is_worse():
    good = [(0.9, True), (0.1, False)]
    bad = [(0.9, False), (0.1, True)]
    assert aurc(bad) > aurc(good)


def test_ties_do_not_depend_on_row_order():
    a = [(0.5, True), (0.5, False), (0.2, False)]
    b = [(0.5, False), (0.5, True), (0.2, False)]
    assert aurc(a) == aurc(b)
    assert risk_coverage(a) == risk_coverage(b) == [(2 / 3, 0.5), (1.0, 2 / 3)]


def test_ece_zero_when_calibrated():
    assert ece([(1.0, True), (0.0, False)]) == 0.0


def _run(task, submission, post, hyps):
    return {"task": task, "submission": submission, "posteriors": post, "hypotheses": hyps}


HYPS = {"H1": {"service": "a", "system_level": "Application", "fault_type": "Code Defect"},
        "H2": {"service": "b", "system_level": "Application", "fault_type": "Code Defect"},
        "U1": {"service": "a", "system_level": "Unknown", "fault_type": "Unknown", "catch_all": True},
        "H0": {"service": None, "system_level": "None", "fault_type": "None"}}
POST = {"H1": 0.4, "H2": 0.2, "U1": 0.2, "H0": 0.2}


def test_confidence_is_for_the_submitted_answer():
    assert abs(answer_confidence(_run("detection", 'submit("Yes")', POST, HYPS)) - 0.8) < 1e-12
    assert abs(answer_confidence(_run("detection", 'submit("No")', POST, HYPS)) - 0.2) < 1e-12
    # localization: P(service | fault), catch-all counts for its service
    assert abs(answer_confidence(_run("localization", 'submit(["a"])', POST, HYPS)) - 0.75) < 1e-12
    # analysis: specific hypotheses naming (level, type), catch-alls excluded
    sub = 'submit({"system_level": "Application", "fault_type": "Code Defect"})'
    assert abs(answer_confidence(_run("analysis", sub, POST, HYPS)) - 0.75) < 1e-12
    assert answer_confidence(_run("localization", "submit(None)", POST, HYPS)) == 0.0
    assert answer_confidence({"task": "detection", "submission": None}) is None
