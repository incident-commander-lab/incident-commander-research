"""Model ladder: log parsing and the fake-cluster episode (no LLM, no cluster)."""
from ic.bench import ladder
from ic.bench.decide import BenchHypothesis

SVC = "NAME   TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)   AGE\ngeo    ClusterIP   10.0.0.1     <none>        8083/TCP  1m\nuser   ClusterIP   10.0.0.2     <none>        8086/TCP  1m\n"
PODS = ("deployment.apps/geo    0/1   1   0   1m\ndeployment.apps/user   1/1   1   1   1m\n===END-WORKLOADS===\n"
        "NAME                 READY   STATUS             RESTARTS   AGE\ngeo-5d8f-abcde        0/1     CrashLoopBackOff   3          1m\n"
        "user-66d5-fghij       1/1     Running            0          1m\n")
DESC = "Service Details:\nNamespace: test-hotel\nYou are assigned to localize faults in a deployed service.\n"


def test_listings_parsed_from_log():
    log = "Helm stuff\nNAME: prometheus\n\n== Start Workload ==\n" + SVC + "\n" + PODS + "\n== Evaluation ==\n"
    svc, pods = ladder._listings(log)
    assert svc.startswith("NAME   TYPE") and "user" in svc
    assert "===END-WORKLOADS===" in pods and "CrashLoopBackOff" in pods


def test_episode_uses_bank_labels_and_marks_missing_checks():
    def gen(problem, services, probes, observations, findings=None):
        return [BenchHypothesis("H1", "geo", "Code Defect", "Application", "geo crashes", 0.6,
                                {"log_signature:geo": "crash_errors"}),
                BenchHypothesis("H2", "user", "Misconfiguration", "Application", "user misconfigured", 0.4,
                                {"log_signature:user": "crash_errors"})], {}
    inp = {"desc": DESC, "svc": SVC, "pods": PODS, "task": "localization"}
    bank = {"log_signature:geo": {"outcome": "crash_errors", "eps": None}}
    ep = ladder.episode("p", inp, bank, 2, gen)
    assert ep["submission"] == 'submit(["geo"])'
    assert ep["n_hyps"] == 2 and ep["llm_error"] is None
