"""Pure-LLM baselines for the benchmark (conditions A and B).

A — ReActAgent: the standard LLM agent. Sees the task description and the full
    AIOpsLab API (any shell command, logs, metrics, traces) and decides every
    step itself, including when to submit. No explicit hypotheses.

B — LLMChooserAgent: identical to the VoI agent (same discovery, same LLM
    hypotheses, same probe catalog) except that the LLM — not expected
    information gain — picks each next probe and decides when to stop and which
    hypothesis to submit. B vs C therefore isolates the decision layer.

Both record token usage through ic.llm.USAGE, like the VoI agent.
"""
from __future__ import annotations

import re
import time
from typing import Literal, Optional

from pydantic import BaseModel

from .. import llm
from .agent import VoIAgent, _block, task_type

MAX_OBSERVATION_CHARS = 8000  # environment replies are truncated identically for A


def _first_code_block(text: str) -> Optional[str]:
    m = re.search(r"```[a-zA-Z]*\s*\n(.*?)\n\s*```", text, re.DOTALL)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------- #
# A — ReAct
# --------------------------------------------------------------------------- #
class ReActAgent:
    policy = "react"

    def __init__(self, *, temperature: float = 0.2, **_):
        self.temperature = temperature
        self.result: dict = {}

    def init_context(self, problem_desc: str, instructions: str = "", apis=None):
        api_docs = "\n\n".join(f"{k}\n{v}" for k, v in (apis or {}).items())
        self.system = (f"{problem_desc.strip()}\n\nAvailable APIs:\n{api_docs}\n\n"
                       f"{instructions.strip()}\n\nThink briefly, then give exactly ONE API call "
                       f"in a single markdown code block.")
        self.task = task_type(problem_desc)
        self.messages: list[dict] = []
        self.actions: list[str] = []   # every API call issued, for repeated-action counting
        self.steps = 0
        self.format_errors = 0
        self.t0 = time.monotonic()
        llm.reset_usage()

    async def get_action(self, state: str) -> str:
        return self.step(state)

    def step(self, state: str) -> str:
        obs = (state or "Begin.")[:MAX_OBSERVATION_CHARS]
        self.messages.append({"role": "user", "content": obs})
        try:
            reply = llm.chat(self.system, self.messages, temperature=self.temperature)
        except Exception as e:  # LLM unavailable: fail this problem honestly, keep the run alive
            call = {"detection": 'submit("No")', "analysis": "submit(None)"}.get(self.task, "submit([])")
            self._finish(call)
            self.result["llm_error"] = str(e).splitlines()[-1][:300]
            return _block(call)
        self.messages.append({"role": "assistant", "content": reply})
        self.steps += 1
        call = _first_code_block(reply)
        if call is None:
            self.format_errors += 1
            call = 'exec_shell("echo no action given")'
        self.actions.append(call)
        if call.startswith("submit"):
            self._finish(call)
        return _block(call)

    def _finish(self, call: str):
        self.result = {
            "policy": self.policy, "task": self.task, "submission": call,
            "probes_run": self.steps - 1, "format_errors": self.format_errors,
            "actions": [a for a in self.actions if not a.startswith("submit")],
            "llm_usage": dict(llm.USAGE),
            "wall_seconds": round(time.monotonic() - self.t0, 2),
            "trace": [m["content"][:500] for m in self.messages if m["role"] == "assistant"],
        }


# --------------------------------------------------------------------------- #
# B — same hypotheses and probes as the VoI agent; the LLM decides
# --------------------------------------------------------------------------- #
class _Choice(BaseModel):
    action: Literal["probe", "stop"]
    probe: Optional[str] = None       # probe key when action == "probe"
    hypothesis: Optional[str] = None  # hypothesis id when action == "stop"
    reason: str = ""


class LLMChooserAgent(VoIAgent):
    """Condition B. Inherits discovery, hypothesis generation and probe labelling
    from VoIAgent; replaces only probe selection, stopping and the final answer."""

    def __init__(self, policy: str = "llm_choose", **kw):
        kw.pop("seed", None)
        super().__init__("voi", **kw)  # Investigation still tracks posteriors (for Brier)
        self.policy = "llm_choose"
        self.chosen: Optional[str] = None

    def _next(self) -> str:
        inv = self.inv
        unrun = [k for k in self.probes if k not in {s.probe for s in inv.steps}]
        if not unrun or len(inv.steps) >= inv.max_probes:
            inv.stop_reason = "no_probes_left" if not unrun else "budget"
            return self._stop(None)
        choice = self._ask(unrun)
        if choice.action == "stop" or choice.probe not in unrun:
            inv.stop_reason = "llm_stop" if choice.action == "stop" else "llm_invalid_probe"
            return self._stop(choice.hypothesis)
        self.pending = choice.probe
        return _block(self.probes[choice.probe].call(self.ns))

    def _ask(self, unrun: list[str]) -> _Choice:
        hyps = "\n".join(
            f"  {h.id}: service={h.service} fault={h.fault_type} — {h.claim}\n"
            f"      predicts: {h.predictions}" for h in self.inv.hyps.values())
        done = "\n".join(f"  {s.probe} = {s.outcome}" for s in self.inv.steps) or "  (none yet)"
        system = ("You are directing an incident investigation. Choose the single most useful "
                  "next probe, or stop and name the hypothesis you are confident is correct. "
                  "Fewer probes is better, but a wrong answer is worse than an extra probe.")
        user = (f"Hypotheses:\n{hyps}\n\nProbe results so far:\n{done}\n\n"
                f"Probes you may run next: {unrun}")
        try:
            return llm.json_call(system, user, _Choice, max_tokens=1500, temperature=0.2)
        except RuntimeError:
            return _Choice(action="stop", reason="llm_error")

    def _stop(self, hypothesis_id: Optional[str]) -> str:
        if hypothesis_id not in self.inv.hyps:
            hypothesis_id = None
        self.chosen = hypothesis_id or self.inv.leader().id
        self.phase = "done"
        self._finish()
        self.result["llm_chosen"] = self.chosen
        self.result["posterior_leader"] = self.inv.leader().id
        return _block(self._submission())

    def _submission(self) -> str:
        return self._answer(self.inv.hyps[self.chosen or self.inv.leader().id])


def make_agent(policy: str, seed: int = 0):
    """One factory for all five conditions."""
    if policy == "react":
        return ReActAgent()
    if policy == "llm_choose":
        return LLMChooserAgent()
    return VoIAgent(policy, seed=seed)


POLICIES = ("react", "llm_choose", "voi", "random", "exhaustive")
