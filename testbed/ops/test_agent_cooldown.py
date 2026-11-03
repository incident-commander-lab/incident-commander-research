"""The 180 s cooldown (3 Nov): an alert during an investigation is dropped, not queued to run
straight after it, and the cooldown starts when the incident is done, for both agents."""
import threading
import time

import pytest


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("INCIDENTS_DIR", str(tmp_path))
    from testbed_ops import agent as A
    monkeypatch.setattr(A, "_busy_until", 0.0)
    monkeypatch.setattr(A, "OUT", tmp_path)
    return A


def _post(A, name="WorkerLag"):
    from fastapi.testclient import TestClient
    return TestClient(A.app).post("/alert", json={"alerts": [
        {"status": "firing", "labels": {"alertname": name}, "annotations": {}, "startsAt": "x"}]}).json()


def test_an_alert_during_an_investigation_is_dropped_and_the_cooldown_follows(agent, monkeypatch):
    started, release, done = threading.Event(), threading.Event(), []
    def slow(alerts):
        started.set(); release.wait(5); done.append(alerts[0]["name"])
    monkeypatch.setattr(agent, "handle", slow)
    monkeypatch.setattr(agent, "AGENT_KIND", "rule")
    assert "queued" in _post(agent, "First")
    assert started.wait(5)
    assert "ignored" in _post(agent, "During")          # mid-investigation: dropped, not queued
    release.set()
    for _ in range(50):
        if done and agent._busy_until != float("inf"):
            break
        time.sleep(0.05)
    assert "ignored" in _post(agent, "After")           # straight after: inside the cooldown
    time.sleep(0.3)
    assert done == ["First"]
    assert agent._busy_until > time.time() + agent.COOLDOWN_SECONDS - 5


def test_a_crashed_investigation_still_starts_the_cooldown(agent, monkeypatch):
    finished = threading.Event()
    def crash(alerts):
        finished.set(); raise RuntimeError("boom")
    monkeypatch.setattr(agent, "handle", crash)
    monkeypatch.setattr(agent, "AGENT_KIND", "rule")
    assert "queued" in _post(agent)
    assert finished.wait(5)
    for _ in range(50):
        if agent._busy_until != float("inf"):
            break
        time.sleep(0.05)
    assert time.time() < agent._busy_until < float("inf")
