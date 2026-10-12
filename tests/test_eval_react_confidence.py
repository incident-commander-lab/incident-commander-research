import json

from ic.eval.calibration import answer_confidence, merge_posthoc
from ic.eval.react_confidence import _Verdict, elicit, key, observations, prompt

RUN = {"policy": "react", "model": "qwen3:8b", "problem": "p-localization-1", "seed": 0,
       "task": "localization", "actions": ['exec_shell("kubectl get pods")'],
       "submission": 'submit(["user"])', "aiopslab_results": {"success": True}}


def test_observations_keep_only_what_the_agent_saw():
    log = "setup noise\n== Start Workload ==\n\x1b[32mpod user crashloop\x1b[0m\n== Evaluation ==\nResults: success"
    obs = observations(log)
    assert "pod user crashloop" in obs and "\x1b" not in obs
    assert "setup noise" not in obs and "Results" not in obs


def test_prompt_never_shows_the_grade():
    p = prompt(RUN, "pod user crashloop")
    assert 'submit(["user"])' in p and "success" not in p.lower()


def test_elicit_calls_the_llm_only_for_answered_runs_and_resumes(tmp_path):
    (tmp_path / "qwen3_8b__react__p-localization-1.log").write_text(
        "== Start Workload ==\nuser 500s\n== Evaluation ==", encoding="utf-8")
    silent = dict(RUN, problem="q-detection-1", task="detection", submission=None)
    calls = []

    def ask(user):
        calls.append(user)
        return _Verdict(p_correct=0.7, reason="logs point at user")

    out = tmp_path / "conf.jsonl"
    assert elicit([RUN, silent], tmp_path, out, ask) == 2
    assert len(calls) == 1 and "user 500s" in calls[0]
    lines = {json.loads(l)["key"]: json.loads(l) for l in out.read_text().splitlines()}
    assert lines[key(silent)]["confidence"] == 0.0 and lines[key(silent)]["method"] == "no_answer"
    assert lines[key(RUN)]["method"] == "posthoc_llm"
    assert elicit([RUN, silent], tmp_path, out, ask) == 0      # resumes: nothing left to do


def test_merged_confidence_reaches_the_calibration_table(tmp_path):
    out = tmp_path / "conf.jsonl"
    out.write_text(json.dumps({"key": key(RUN), "confidence": 0.7}) + "\n")
    rows = [dict(RUN)]
    assert merge_posthoc(rows, [str(out)]) == 1
    assert answer_confidence(rows[0]) == 0.7
