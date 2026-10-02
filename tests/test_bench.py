"""Offline tests for ic/bench — no cluster, no LLM.

A fake Kubernetes environment answers the agent's calls; a fake generator
stands in for the LLM. This checks the protocol, the labelers, the decision
policies and the anti-hallucination filter.
"""
import asyncio

from ic import llm
from ic.bench import hypotheses as hg
from ic.bench.agent import VoIAgent, task_type
from ic.bench.decide import BenchHypothesis, Investigation
from ic.bench.probes import (instances, label_deployment, label_endpoints,
                             label_event_reason, label_log_signature, label_pod_health,
                             parse_services)

NS = "test-hotel-reservation"
SERVICES = ["frontend", "geo", "profile", "search"]
PROBLEM = f"""You are an expert DevOps engineer assigned to localize faults in a deployed service.
Service Details:
Service Name: Hotel Reservation
Namespace: {NS}
"""

SVC_OUT = "NAME  TYPE  CLUSTER-IP  PORT(S)\n" + "\n".join(
    f"{s}  ClusterIP  10.0.0.{i}  8080/TCP" for i, s in enumerate(SERVICES))
PODS_OUT = "\n".join([
    "frontend-7d9f8c6b5-abcde 1/1   Running            0   5m",
    "geo-5c4b9d7f6-xyz12      0/1   CrashLoopBackOff   6   5m",
    "profile-66aa5b4c3-defgh  1/1   Running            0   5m",
    "search-12bc3d4e5-ghijk   1/1   Running            0   5m",
])


def fake_env(call: str) -> str:
    """Answer each probe call the way kubectl would with `geo` crash-looping."""
    if "get pods" in call:
        svc = call.split("grep ")[-1].rstrip('")')
        return "\n".join(l for l in PODS_OUT.splitlines() if l.startswith(svc))
    if "get deploy" in call:
        svc = call.split("get deploy ")[1].split()[0]
        return f"{svc}  {'0/1' if svc == 'geo' else '1/1'}  1  1  5m"
    if "get endpoints" in call:
        svc = call.split("get endpoints ")[1].split()[0]
        return f"{svc}  {'<none>' if svc == 'geo' else '10.1.0.5:8080'}  5m"
    if "get events" in call:
        svc = call.split("grep ")[-1].rstrip('")')
        return "1m  Warning  BackOff  pod/geo-5c4b9d7f6-xyz12  Back-off restarting" if svc == "geo" else ""
    if call.startswith("get_logs"):
        svc = call.split('", "')[1].rstrip('")')
        return "panic: config missing\nerror x\nerror y" if svc == "geo" else "ok\nserving"
    raise AssertionError(call)


def fake_generate(problem, services, probes, observations, findings=None):
    def h(i, svc, prior):
        return BenchHypothesis(f"H{i}", svc, "Misconfiguration", "Application",
                               f"{svc} is broken", prior,
                               {f"pod_health:{svc}": "unhealthy", f"endpoints:{svc}": "no_endpoints",
                                **{f"pod_health:{o}": "healthy" for o in services if o != svc}})
    # The LLM's prior favours the WRONG service; evidence must overturn it.
    return [h(1, "search", 0.5), h(2, "geo", 0.3), h(3, "profile", 0.2),
            hg.no_fault_hypothesis(probes)], {}


def run_agent(policy: str) -> VoIAgent:
    agent = VoIAgent(policy, generate=fake_generate, max_probes=40)
    agent.init_context(PROBLEM)
    state = ""
    for _ in range(60):
        action = asyncio.run(agent.get_action(state))
        call = action.strip("`\n ")
        if call.startswith("submit"):
            return agent
        if "get svc" in call:
            state = SVC_OUT
        elif call.endswith(f'get pods -n {NS}")'):
            state = PODS_OUT
        else:
            state = fake_env(call)
    raise AssertionError("agent never submitted")


# --------------------------------------------------------------------------- #
def test_labelers():
    assert label_pod_health("geo-1  0/1  CrashLoopBackOff  6  5m", "geo") == "unhealthy"
    assert label_pod_health("geo-1  1/1  Running  0  5m", "geo") == "healthy"
    assert label_pod_health("", "geo") == "unhealthy"
    assert label_endpoints("geo  <none>  5m", "geo") == "no_endpoints"
    assert label_endpoints("geo  10.1.0.5:8080  5m", "geo") == "has_endpoints"
    assert label_deployment("geo  0/0  0  0  5m", "geo") == "degraded"
    assert label_log_signature("all good\nserving", "geo") == "clean"
    assert label_log_signature("one connection refused only", "geo") == "clean"  # below minimum
    assert label_log_signature("Error: Your service does not exist.", "geo") == "crash_errors"
    assert label_event_reason("1m Warning FailedScheduling pod/geo-1 0/2 nodes", "geo") == "scheduling"
    assert label_event_reason("1m Warning BackOff pod/geo-1 restarting", "geo") == "crash_backoff"
    assert label_event_reason("1m Warning FailedMount pod/geo-1 pvc missing", "geo") == "storage"
    assert label_event_reason("1m Warning BackOff pod/search-1", "geo") == "none"
    assert parse_services(SVC_OUT) == SERVICES


def test_task_type_and_namespace():
    assert task_type(PROBLEM) == "localization"
    assert hg.parse_namespace(PROBLEM) == NS


def test_voi_localizes_against_wrong_prior():
    agent = run_agent("voi")
    assert agent.result["submission"] == 'submit(["geo"])'
    assert agent.result["stop_reason"] == "no_probe_worth_its_cost"


def test_voi_uses_fewer_probes_than_exhaustive():
    voi, exhaustive = run_agent("voi"), run_agent("exhaustive")
    assert exhaustive.result["submission"] == 'submit(["geo"])'
    assert voi.result["probes_run"] < exhaustive.result["probes_run"]
    # pod_health for every service is read for free from the discovery pod listing
    assert exhaustive.result["free_observations"] == len(SERVICES)
    assert exhaustive.result["probes_run"] == len(SERVICES) * 5 - len(SERVICES)


def test_random_policy_still_stops():
    agent = run_agent("random")
    assert agent.result["stop_reason"] == "no_probe_worth_its_cost"


def test_surprise_detects_contradicted_list():
    probes = instances(["a", "b"])
    hyps = [BenchHypothesis("H1", "a", "x", "y", "", 0.5, {"pod_health:a": "unhealthy"}),
            BenchHypothesis("H2", "b", "x", "y", "", 0.5, {"pod_health:a": "healthy",
                                                             "pod_health:b": "unhealthy"})]
    inv = Investigation(hyps, probes)
    inv.observe("pod_health:a", "healthy")
    assert not inv.surprise()            # H2 still consistent
    inv.observe("pod_health:b", "healthy")
    assert inv.surprise()                # both contradicted


def test_filter_drops_hallucinated_services_and_probes():
    probes = instances(["geo"])
    raw = hg._Response(hypotheses=[
        hg._Hypothesis(id="H1", service="geo", system_level="Application",
                       fault_type="Misconfiguration", claim="", confidence=0.5,
                       predictions=[hg._Prediction(probe="pod_health:geo", outcome="unhealthy"),
                                    hg._Prediction(probe="disk_io:geo", outcome="slow"),
                                    hg._Prediction(probe="endpoints:geo", outcome="maybe")]),
        hg._Hypothesis(id="H2", service="payments", system_level="Application",
                       fault_type="Code Defect", claim="", confidence=0.5,
                       predictions=[hg._Prediction(probe="pod_health:geo", outcome="healthy")]),
    ])
    kept, meta = hg._clean(raw, ["geo"], probes)
    assert [h.service for h in kept] == ["geo"]
    assert kept[0].predictions == {"pod_health:geo": "unhealthy"}
    assert meta == {"dropped_hypotheses": 1, "dropped_predictions": 2}


def test_vllm_provider_config(monkeypatch):
    monkeypatch.setenv("IC_PROVIDER", "vllm")
    monkeypatch.setenv("IC_VLLM_BASE_URL", "http://gpu-box:8001/v1")
    chain = llm.active_chain()
    assert [p.name for p in chain] == ["vllm"]
    assert chain[0].api == "openai" and chain[0].has_key()
    assert llm._strip_think("<think>hmm</think>{\"a\":1}") == '{"a":1}'


# --------------------------------------------------------------------------- #
# Baselines A and B
# --------------------------------------------------------------------------- #
from ic.bench import baselines


def test_react_baseline_submits_and_counts_tokens(monkeypatch):
    replies = iter(['I will look at pods.\n```\nexec_shell("kubectl get pods -n x")\n```',
                    '<think>geo is crashing</think>Geo.\n```\nsubmit(["geo"])\n```'])

    def fake_chat(system, messages, **kw):
        llm._record(100, 10)
        return llm._strip_think(next(replies))
    monkeypatch.setattr(llm, "chat", fake_chat)

    agent = baselines.ReActAgent()
    agent.init_context(PROBLEM, "instructions", {"exec_shell": "doc"})
    assert "kubectl get pods" in asyncio.run(agent.get_action(""))
    assert asyncio.run(agent.get_action(PODS_OUT)) == '```\nsubmit(["geo"])\n```'
    assert agent.result["probes_run"] == 1
    assert agent.result["llm_usage"]["input_tokens"] == 200


def test_llm_chooser_baseline_uses_llm_decisions(monkeypatch):
    choices = iter([baselines._Choice(action="probe", probe="endpoints:frontend"),
                    baselines._Choice(action="probe", probe="endpoints:geo"),
                    baselines._Choice(action="stop", hypothesis="H2")])
    monkeypatch.setattr(llm, "json_call", lambda *a, **k: next(choices))

    agent = baselines.LLMChooserAgent(generate=fake_generate)
    agent.init_context(PROBLEM)
    state, action = "", ""
    for _ in range(10):
        action = asyncio.run(agent.get_action(state)).strip("`\n ")
        if action.startswith("submit"):
            break
        state = SVC_OUT if "get svc" in action else PODS_OUT if action.endswith(f'get pods -n {NS}")') else fake_env(action)
    assert action == 'submit(["geo"])'
    assert agent.result["probes_run"] == 2
    assert agent.result["stop_reason"] == "llm_stop"
    assert agent.result["llm_chosen"] == "H2"


def test_make_agent_covers_all_conditions():
    for p in baselines.POLICIES:
        assert baselines.make_agent(p) is not None


# --------------------------------------------------------------------------- #
# Fault-type checks
# --------------------------------------------------------------------------- #
LOG_SAMPLES = {
    "auth_errors": "mongo: command find requires authentication\nerror: not authorized on geo-db\nnot authorized to execute command",
    "config_errors": "invalid argument --port=abc\nfailed to load config: missing env DB_URL\nconfig error: key not found",
    "connection_errors": "dial tcp 10.0.0.9:27017: connection refused\nrpc error: deadline exceeded\ni/o timeout",
    "storage_errors": "write failed: no space left on device\nerror: database geo-db does not exist\nread-only file system",
    "crash_errors": "panic: runtime error: invalid memory address or nil pointer dereference\ngoroutine 1 [running]:\nfatal error: unexpected signal",
}


def test_log_signature_categories():
    for expected, logs in LOG_SAMPLES.items():
        assert label_log_signature(logs, "geo") == expected, expected


def test_fault_type_is_discriminated_on_same_service():
    """Two causes on the SAME service, differing only in fault type: pod-level
    probes cannot separate them, so VoI must reach for log_signature."""
    probes = instances(["geo"])
    common = {"pod_health:geo": "unhealthy"}
    hyps = [BenchHypothesis("H1", "geo", "Authentication Issue", "Application", "creds revoked", 0.5,
                            {**common, "log_signature:geo": "auth_errors"}),
            BenchHypothesis("H2", "geo", "Misconfiguration", "Application", "bad config", 0.5,
                            {**common, "log_signature:geo": "config_errors"}),
            hg.no_fault_hypothesis(probes, prior=0.05)]
    inv = Investigation(hyps, probes)
    first = inv.next_probe()
    # pod_health only separates the faults from "no fault"; log_signature separates the two faults
    assert inv.eig("log_signature:geo") > inv.eig("pod_health:geo")
    assert first == "log_signature:geo"
    inv.observe(first, label_log_signature(LOG_SAMPLES["auth_errors"], "geo"))
    assert inv.leader().fault_type == "Authentication Issue"


def test_analysis_task_submits_fault_type():
    agent = VoIAgent("voi", generate=fake_generate)
    agent.init_context(PROBLEM.replace("localize faults", "do root cause analysis"))
    agent.inv = Investigation([BenchHypothesis("H1", "geo", "Authentication Issue", "Application", "", 1.0)],
                              instances(["geo"]))
    assert agent._submission() == 'submit({"system_level": "Application", "fault_type": "Authentication Issue"})'


def test_llm_failure_submits_and_records_error():
    def broken(*a, **k):
        raise RuntimeError("json_call: every provider failed\n  401 Unauthorized")
    agent = VoIAgent("voi", generate=broken)
    agent.init_context(PROBLEM)
    asyncio.run(agent.get_action(""))
    asyncio.run(agent.get_action(SVC_OUT))
    assert asyncio.run(agent.get_action(PODS_OUT)) == "```\nsubmit([])\n```"
    assert "401" in agent.result["llm_error"]
    assert asyncio.run(agent.get_action("anything")) == "```\nsubmit([])\n```"


def test_pod_owner_and_exact_service_matching():
    from ic.bench.probes import pod_owner
    assert pod_owner("geo-c47ff745-5bgdz") == "geo"
    assert pod_owner("mongodb-geo-5ff578bcb8-zbm6t") == "mongodb-geo"
    assert pod_owner("pod/user-timeline-service-6c9b8d7f5-abcde") == "user-timeline-service"
    assert pod_owner("mongodb-geo-0") == "mongodb-geo"
    pods = ("geo-c47ff745-5bgdz        1/1 Running 0 1m\n"
            "mongodb-geo-5ff578bcb8-zbm6t 0/1 Error 3 1m")
    assert label_pod_health(pods, "geo") == "healthy"          # mongodb-geo's crash is not geo's
    assert label_pod_health(pods, "mongodb-geo") == "unhealthy"


def test_seen_crash_rules_out_no_fault_and_one_log_cannot_flip_it():
    """Regression for the first AIOpsLab run: the crashed pod was visible in
    discovery, then one 'clean' log reading pushed 'no fault' to 90% and the
    agent stopped. Discovery evidence + noisier log probes must prevent that."""
    def gen(problem, services, probes, observations, findings=None):
        mk = lambda i, ft, sig, p: BenchHypothesis(f"H{i}", "geo", ft, "Application", "", p,
                                                   {"log_signature:geo": sig})
        return [mk(1, "Misconfiguration", "config_errors", 0.3), mk(2, "Dependency Problem", "connection_errors", 0.25),
                mk(3, "Code Defect", "crash_errors", 0.2), hg.no_fault_hypothesis(probes)], {}
    agent = VoIAgent("voi", generate=gen)
    agent.init_context(PROBLEM)
    agent.step(""); agent.step(SVC_OUT); agent.step(PODS_OUT)
    assert agent.inv.posteriors[hg.NO_FAULT_ID] < 0.05      # geo CrashLoopBackOff already seen
    agent.inv.observe("log_signature:geo", "clean")
    assert agent.inv.posteriors[hg.NO_FAULT_ID] < 0.5
    assert agent.inv.leader().service == "geo"


def test_parser_accepts_prose_around_json():
    from pydantic import BaseModel

    class M(BaseModel):
        a: int
    assert llm._parse_into(M, 'Looking at the pods, geo is failing.\n{"a": 1}\nThat is my answer.').a == 1
    assert llm._parse_into(M, '```json\n{"a": 2}\n```').a == 2


def test_rate_limit_is_retried_on_same_model(monkeypatch):
    import httpx
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        req = httpx.Request("POST", url)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, request=req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}, request=req)
    monkeypatch.setattr(httpx, "post", fake_post)
    llm.reset_usage()
    r = llm._post_with_retry("https://x/v1/chat/completions", {}, {"model": "m:free"})
    assert r.status_code == 200 and calls == ["m:free", "m:free"] and llm.USAGE["retries"] == 1


def test_parser_takes_first_valid_object_and_ignores_trailing_text():
    from pydantic import BaseModel

    class M(BaseModel):
        a: int
    assert llm._parse_into(M, '{"a": 1}\n{"a": 2}').a == 1                       # two objects
    assert llm._parse_into(M, '{"a": 3}\n\nNote: H1 is most likely.').a == 3     # trailing prose
    assert llm._parse_into(M, 'Example: {"b": 0}. Answer: {"a": 4} done').a == 4  # skip non-matching


def test_parse_services_ignores_aiopslab_preamble():
    out = "Please take the next action\n" + SVC_OUT
    assert parse_services(out) == SERVICES


# --------------------------------------------------------------------------- #
# LLM log reader (sensor)
# --------------------------------------------------------------------------- #
from ic.bench import sensors

GEO_LOG = ("\x1b[90m2026-10-02T09:34:20Z\x1b[0m INF cmd/geo/main.go:36 > Read database URL: mongodb-geo:27777\n"
           "INF cmd/geo/main.go:37 > Initializing DB connection...\n")


def test_sensor_accepts_grounded_quote_and_rejects_invented_one(monkeypatch):
    outcomes = ("auth_errors", "config_errors", "connection_errors", "storage_errors", "crash_errors", "clean")
    monkeypatch.setattr(sensors, "json_call", lambda *a, **k: sensors._Reading(
        label="config_errors", evidence="INF cmd/geo/main.go:36 > Read database URL: mongodb-geo:27777"))
    label, meta = sensors.read_log(GEO_LOG, "geo", outcomes, "")
    assert label == "config_errors" and meta["status"] == "accepted"      # ANSI codes stripped before matching
    monkeypatch.setattr(sensors, "json_call", lambda *a, **k: sensors._Reading(
        label="auth_errors", evidence="authentication failed for user geo"))
    label, meta = sensors.read_log(GEO_LOG, "geo", outcomes, "")
    assert label is None and meta["status"] == "rejected_unquoted"        # quote not in the log


def test_agent_uses_llm_reading_with_its_own_noise_and_keeps_regex_label():
    def fake_read(raw, svc, outcomes, describe):
        return "config_errors", {"sensor": "llm", "status": "accepted"}

    def gen(problem, services, probes, observations, findings=None):
        mk = lambda i, ft, sig: BenchHypothesis(f"H{i}", "geo", ft, "Application", "", 0.5,
                                                {"log_signature:geo": sig})
        return [mk(1, "Misconfiguration", "config_errors"), mk(2, "Code Defect", "crash_errors"),
                hg.no_fault_hypothesis(probes)], {}
    agent = VoIAgent("voi", generate=gen, log_reader="llm", read_log=fake_read)
    agent.init_context(PROBLEM)
    agent.step(""); agent.step(SVC_OUT)
    first = agent.step(PODS_OUT)
    assert "get_logs" in first                       # VoI asks for the discriminating log probe
    agent.step("INF Read database URL: mongodb-geo:27777")
    step = [s for s in agent.inv.steps if s.probe == "log_signature:geo"][0]
    assert step.outcome == "config_errors" and step.eps == sensors.LLM_READ_EPS
    assert step.sensor["regex_label"] == "clean"     # what the regex alone would have said
    assert agent.inv.leader().fault_type == "Misconfiguration"


# --------------------------------------------------------------------------- #
# Token cuts (from the pilot's revoke_auth run: 4 LLM calls, 2 of them wasted)
# --------------------------------------------------------------------------- #
def test_knock_on_symptom_does_not_trigger_regeneration():
    probes = instances(["geo", "frontend"])
    hyps = [BenchHypothesis("H1", "geo", "Authentication Issue", "Application", "", 0.6,
                            {"log_signature:geo": "auth_errors"}),
            BenchHypothesis("H2", "geo", "Misconfiguration", "Application", "", 0.4,
                            {"log_signature:geo": "config_errors"})]
    inv = Investigation(hyps, probes)
    inv.observe("log_signature:frontend", "connection_errors")   # caller suffers because geo is down
    assert not inv.surprise()                                     # implied default, not an explicit prediction
    inv.observe("log_signature:geo", "clean")
    assert inv.surprise()                                         # explicit predictions all failed


def test_pilot_replay_uses_two_llm_calls_not_four():
    calls = {"generate": 0, "read": 0}

    def gen(problem, services, probes, observations, findings=None):
        calls["generate"] += 1
        mk = lambda i, ft, sig, p: BenchHypothesis(f"H{i}", "geo", ft, "Application", "", p,
                                                   {"log_signature:geo": sig})
        return [mk(1, "Authentication Issue", "auth_errors", 0.4), mk(2, "Misconfiguration", "config_errors", 0.35),
                mk(3, "Code Defect", "crash_errors", 0.25), hg.no_fault_hypothesis(probes)], {}

    def read(raw, svc, outcomes, describe):
        calls["read"] += 1
        return "auth_errors", {"sensor": "llm", "status": "accepted"}

    replies = {"geo": "INF Initializing DB connection...\nINF reading collection",       # regex: clean
               "frontend": "dial tcp geo:8083: connection refused\nrpc error: i/o timeout"}  # regex: connection_errors
    agent = VoIAgent("voi", generate=gen, log_reader="llm", read_log=read)
    agent.init_context(PROBLEM)
    action = agent.step(""); action = agent.step(SVC_OUT); action = agent.step(PODS_OUT)
    for _ in range(12):
        call = action.strip("`\n ")
        if call.startswith("submit"):
            break
        svc = call.split('", "')[1].rstrip('")') if call.startswith("get_logs") else None
        action = agent.step(replies.get(svc, "") if svc else fake_env(call))
    assert call == 'submit(["geo"])'
    assert calls == {"generate": 1, "read": 1}       # was 2 + 2 in the pilot
    assert agent.inv.leader().fault_type == "Authentication Issue"


def test_log_reader_skips_logs_the_regex_already_labels():
    reads = []
    agent = VoIAgent("voi", generate=fake_generate, log_reader="llm",
                     read_log=lambda *a: (reads.append(a[1]), ("auth_errors", {"status": "accepted"}))[1])
    agent.init_context(PROBLEM)
    agent.step(""); agent.step(SVC_OUT); agent.step(PODS_OUT)
    agent.pending = "log_signature:frontend"
    agent._observe_pending("dial tcp geo:8083: connection refused\nrpc error: i/o timeout")
    assert reads == []                                # regex said connection_errors: no LLM call
    agent.pending = "log_signature:geo"
    agent._observe_pending("INF Initializing DB connection...")
    assert reads == ["geo"]                           # regex said clean: LLM reads it


# --------------------------------------------------------------------------- #
# The single VoI rule: choose argmax VoI, stop when max VoI <= 0
# --------------------------------------------------------------------------- #
def test_voi_rule_chooses_argmax_and_stops_at_zero():
    probes = instances(["geo"])
    hyps = [BenchHypothesis("H1", "geo", "Authentication Issue", "Application", "", 0.5,
                            {"log_signature:geo": "auth_errors"}),
            BenchHypothesis("H2", "geo", "Misconfiguration", "Application", "", 0.5,
                            {"log_signature:geo": "config_errors"})]
    inv = Investigation(hyps, probes, lam=0.05)
    k = inv.next_probe()
    assert k == max(probes, key=inv.voi) and inv.voi(k) > 0
    inv.observe(k, "auth_errors")
    assert inv.next_probe() is None and inv.stop_reason == "no_probe_worth_its_cost"
    assert all(inv.voi(x) <= 0 for x in inv._unrun())


def test_higher_lambda_never_runs_more_probes():
    counts = []
    for lam in (0.0, 0.05, 0.2, 1.0):
        agent = VoIAgent("voi", generate=fake_generate, lam=lam, max_probes=40)
        agent.init_context(PROBLEM)
        state = ""
        for _ in range(60):
            call = agent.step(state).strip("`\n ")
            if call.startswith("submit"):
                break
            state = SVC_OUT if "get svc" in call else PODS_OUT if call.endswith(f'get pods -n {NS}")') else fake_env(call)
        counts.append(agent.result["probes_run"])
    assert counts == sorted(counts, reverse=True), counts
