"""AIOpsLab adapter for the LLM-proposes / VoI-decides agent (conditions C, D, E).

AIOpsLab drives an agent turn by turn: it calls `get_action(state)` with the
output of the previous action, and expects back ONE API call in a markdown code
block (`exec_shell(...)`, `get_logs(...)`, `submit(...)`). This class is a small
state machine over that protocol:

  1. discover   — list services, then pods (identical for every condition)
  2. propose    — one LLM call: hypotheses + per-probe predictions
  3. investigate— Investigation.next_probe() → emit its call → label the reply
                  → Bayesian update; regenerate hypotheses once if every one
                  was contradicted
  4. submit     — answer shaped for the task type, from the posterior leader

It never imports aiopslab, so it is unit-testable offline (tests/test_bench.py).
"""
from __future__ import annotations

import json
import time
from typing import Optional

from .. import llm
from . import hypotheses as hyp_gen
from .decide import Investigation
from .probes import _lines_for as _owned_rows, instances, parse_services


def task_type(problem: str) -> str:
    p = problem.lower()
    if "localize faults" in p:
        return "localization"
    if "root cause analysis" in p:
        return "analysis"
    if "detect anomalies" in p:
        return "detection"
    if "mitigate" in p:
        return "mitigation"
    return "unknown"


def _block(call: str) -> str:
    return f"```\n{call}\n```"


class VoIAgent:
    def __init__(self, policy: str = "voi", *, stop_confidence: float = 0.9,
                 min_eig: float = 0.01, max_probes: int = 25, seed: int = 0,
                 max_regenerations: int = 1, generate=hyp_gen.generate):
        self.policy = policy
        self.kw = dict(policy=policy, stop_confidence=stop_confidence,
                       min_eig=min_eig, max_probes=max_probes, seed=seed)
        self.max_regenerations = max_regenerations
        self._generate = generate  # injectable for offline tests
        self.result: dict = {}

    def init_context(self, problem_desc: str, instructions: str = "", apis=None):
        self.problem = problem_desc
        self.task = task_type(problem_desc)
        self.ns = hyp_gen.parse_namespace(problem_desc) or "default"
        self.phase = "list_services"
        self.services: list[str] = []
        self.observations: dict[str, str] = {}
        self.inv: Optional[Investigation] = None
        self.pending: Optional[str] = None
        self.regenerations = 0
        self.gen_meta: list[dict] = []
        self.t0 = time.monotonic()
        llm.reset_usage()

    # ------------------------------------------------------------------ #
    async def get_action(self, state: str) -> str:
        return self.step(state)

    def step(self, state: str) -> str:
        if self.phase == "list_services":
            self.phase = "list_pods"
            return _block(f'exec_shell("kubectl get svc -n {self.ns}")')

        if self.phase == "list_pods":
            self.observations["kubectl get svc"] = state
            self.services = parse_services(state)
            self.phase = "propose"
            return _block(f'exec_shell("kubectl get pods -n {self.ns}")')

        if self.phase == "propose":
            self.observations["kubectl get pods"] = state
            self.probes = instances(self.services)
            try:
                self._propose()
            except Exception as e:  # LLM unavailable: fail this problem honestly, keep the run alive
                return self._give_up(e)
            self.phase = "investigate"
            return self._next()

        if self.phase == "investigate":
            self.inv.observe(self.pending, self.inv.probes[self.pending].label(state), raw=state)
            if self.inv.surprise() and self.regenerations < self.max_regenerations:
                self.regenerations += 1
                try:
                    self._propose(findings=[(s.probe, s.outcome) for s in self.inv.steps])
                except Exception as e:  # keep the current hypotheses and carry on
                    self.gen_meta.append({"regeneration_error": str(e).splitlines()[-1][:200]})
            return self._next()

        return _block(self.result.get("submission") or self._submission())  # already submitted; repeat

    # ------------------------------------------------------------------ #
    def _propose(self, findings=None):
        hyps, meta = self._generate(self.problem, self.services, self.probes,
                                    self.observations, findings)
        self.gen_meta.append(meta)
        old_steps = self.inv.steps if self.inv else self._discovery_evidence()
        self.inv = Investigation(hyps, self.probes, **self.kw)
        for s in old_steps:  # carry evidence over to the new hypothesis set
            self.inv.observe(s.probe, s.outcome, free=s.free, raw=s.raw)

    def _discovery_evidence(self) -> list:
        """The pod listing from discovery already answers pod_health for every
        service. Count it as evidence (free) instead of only showing it to the LLM:
        a crashed pod we have already seen must rule out 'no fault'."""
        from .decide import Step
        pods = self.observations.get("kubectl get pods", "")
        out = []
        for svc in self.services:
            key = f"pod_health:{svc}"
            if key in self.probes and any(True for _ in _owned_rows(pods, svc)):
                out.append(Step(key, self.probes[key].label(pods), 0.0, {}, True,
                                "\n".join(_owned_rows(pods, svc))))
        return out

    def _give_up(self, error: Exception) -> str:
        """No hypotheses could be generated. Submit the 'nothing found' answer so
        AIOpsLab scores the problem (as a failure) and runs its fault recovery."""
        self.phase = "done"
        call = {"detection": 'submit("No")', "analysis": "submit(None)"}.get(self.task, "submit([])")
        self.result = {"policy": self.policy, "task": self.task, "submission": call,
                       "probes_run": 0, "llm_error": str(error).splitlines()[-1][:300],
                       "llm_usage": dict(llm.USAGE),
                       "wall_seconds": round(time.monotonic() - self.t0, 2)}
        return _block(call)

    def _next(self) -> str:
        key = self.inv.next_probe()
        if key is None:
            self.phase = "done"
            self._finish()
            return _block(self._submission())
        self.pending = key
        return _block(self.probes[key].call(self.ns))

    def _submission(self) -> str:
        return self._answer(self.inv.leader())

    def _answer(self, lead) -> str:
        """The submit(...) call for this task type, if `lead` is the root cause."""
        faulty = lead.id != hyp_gen.NO_FAULT_ID
        if self.task == "detection":
            return f'submit("{"Yes" if faulty else "No"}")'
        if self.task == "analysis":
            if not faulty:
                return "submit(None)"
            return "submit(" + json.dumps({"system_level": lead.system_level,
                                           "fault_type": lead.fault_type}) + ")"
        return "submit(" + json.dumps([lead.service] if faulty else []) + ")"

    def _finish(self):
        lead = self.inv.leader()
        self.result = {
            **self.inv.summary(),
            "task": self.task,
            "namespace": self.ns,
            "submission": self._submission(),
            "leader_claim": lead.claim,
            "hypotheses": {h.id: {"service": h.service, "fault_type": h.fault_type,
                                  "prior": h.prior} for h in self.inv.hyps.values()},
            "regenerations": self.regenerations,
            "generation_meta": self.gen_meta,
            "llm_usage": dict(llm.USAGE),
            "wall_seconds": round(time.monotonic() - self.t0, 2),
        }
