"""The decision layer: which probe next, and when to stop.

The LLM proposes hypotheses (ic/bench/hypotheses.py). This module decides,
using the same Bayesian update and expected-information-gain code as live mode
(ic/live/beliefs.py), unchanged.

Selection policies — these are the experiment's conditions C, D and E:
  * "voi"        argmax EIG ÷ cost                         (condition C)
  * "random"     uniform over unrun probes                 (condition D)
  * "exhaustive" every probe in a fixed order              (condition E)

Stopping rule (C and D): stop when the leading hypothesis's posterior reaches
`stop_confidence`, when no unrun probe has EIG ≥ `min_eig`, or when the probe
budget is spent. Exhaustive ignores the first two and runs everything.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from ..live import beliefs
from .probes import ProbeInstance

POLICIES = ("voi", "random", "exhaustive")


@dataclass
class BenchHypothesis:
    id: str
    service: Optional[str]          # None ⇒ the "no fault" hypothesis
    fault_type: str                 # one of AIOpsLab's analysis categories, or "None"
    system_level: str
    claim: str
    prior: float
    predictions: dict[str, str] = field(default_factory=dict)  # probe key → outcome


@dataclass
class Step:
    probe: str
    outcome: str
    eig: float
    posteriors: dict[str, float]
    free: bool = False   # read from discovery output, not a probe the agent paid for
    raw: str = ""        # snippet of the raw reading, for error analysis
    eps: Optional[float] = None   # measurement error used for this reading (None: template default)
    sensor: Optional[dict] = None  # how the label was produced, when not by the regex


class Investigation:
    def __init__(self, hypotheses: list[BenchHypothesis], probes: dict[str, ProbeInstance], *,
                 policy: str = "voi", stop_confidence: float = 0.9, min_eig: float = 0.01,
                 max_probes: int = 25, eps: Optional[float] = None, seed: int = 0):
        if policy not in POLICIES:
            raise ValueError(f"unknown policy {policy!r}")
        self.hyps = {h.id: h for h in hypotheses}
        self.probes = probes
        self.policy = policy
        self.stop_confidence = stop_confidence
        self.min_eig = min_eig
        self.max_probes = max_probes
        self.eps = eps
        self.rng = random.Random(seed)
        total = sum(max(h.prior, 1e-6) for h in hypotheses)
        self.posteriors = {h.id: max(h.prior, 1e-6) / total for h in hypotheses}
        self.steps: list[Step] = []
        self.stop_reason: Optional[str] = None

    # ------------------------------------------------------------------ #
    def _predict(self, h: BenchHypothesis, key: str) -> Optional[str]:
        """A hypothesis's prediction for a probe. Single-root-cause semantics:
        if it is silent about a probe on ANOTHER service, it predicts that probe's
        healthy outcome (what the service looks like without this fault). Silence
        about its own service stays agnostic (None)."""
        if key in h.predictions:
            return h.predictions[key]
        probe = self.probes[key]
        if h.service is not None and probe.service != h.service:
            return probe.outcomes[-1]
        return None

    def _predictions(self, key: str) -> dict[str, Optional[str]]:
        return {hid: self._predict(h, key) for hid, h in self.hyps.items()}

    def eig(self, key: str) -> float:
        probe = self.probes[key]
        return beliefs.expected_information_gain(
            self.posteriors, self._predictions(key), probe.outcomes, self._eps(probe))

    def _eps(self, probe: ProbeInstance) -> float:
        return probe.template.eps if self.eps is None else self.eps

    def _unrun(self) -> list[str]:
        done = {s.probe for s in self.steps}
        return [k for k in self.probes if k not in done]

    # ------------------------------------------------------------------ #
    def next_probe(self) -> Optional[str]:
        """The next probe to run, or None (and sets stop_reason) to stop."""
        candidates = self._unrun()
        if not candidates:
            self.stop_reason = "no_probes_left"
            return None
        if self.paid_steps() >= self.max_probes:
            self.stop_reason = "budget"
            return None
        if self.policy == "exhaustive":
            return candidates[0]
        if max(self.posteriors.values()) >= self.stop_confidence:
            self.stop_reason = "confident"
            return None
        scored = {k: self.eig(k) for k in candidates}
        informative = [k for k, v in scored.items() if v >= self.min_eig]
        if not informative:
            self.stop_reason = "no_discriminating_probe"
            return None
        if self.policy == "random":
            return self.rng.choice(informative)
        return max(informative, key=lambda k: scored[k] / self.probes[k].cost)

    def observe(self, key: str, outcome: str, *, free: bool = False, raw: str = "",
                eps: Optional[float] = None, sensor: Optional[dict] = None) -> None:
        probe = self.probes[key]
        if outcome not in probe.outcomes:
            raise ValueError(f"{key}: outcome {outcome!r} not in {probe.outcomes}")
        eig = self.eig(key)
        e = eps if eps is not None else self._eps(probe)
        self.posteriors = beliefs.update(
            self.posteriors, self._predictions(key), outcome, probe.outcomes, e)
        self.steps.append(Step(key, outcome, eig, dict(self.posteriors), free, raw[:600], eps, sensor))

    def paid_steps(self) -> int:
        return sum(1 for s in self.steps if not s.free)

    # ------------------------------------------------------------------ #
    def leader(self) -> BenchHypothesis:
        return self.hyps[max(self.posteriors, key=self.posteriors.get)]

    def surprise(self) -> bool:
        """True when every hypothesis was contradicted by some observation —
        a sign the true cause is not in the list (triggers re-generation)."""
        if not self.steps:
            return False
        for h in self.hyps.values():
            if all(self._predict(h, s.probe) in (None, s.outcome) for s in self.steps):
                return False
        return True

    def summary(self) -> dict:
        return {
            "policy": self.policy,
            "probes_run": self.paid_steps(),
            "free_observations": len(self.steps) - self.paid_steps(),
            "probe_cost": sum(self.probes[s.probe].cost for s in self.steps if not s.free),
            "stop_reason": self.stop_reason,
            "leader": self.leader().id,
            "posteriors": self.posteriors,
            "trace": [s.__dict__ for s in self.steps],
        }
