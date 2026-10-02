"""The decision layer: which probe next, and when to stop.

The LLM proposes hypotheses (ic/bench/hypotheses.py). This module decides,
using the same Bayesian update and expected-information-gain code as live mode
(ic/live/beliefs.py), unchanged.

One rule for both choosing and stopping — the project's value-of-information
formula, applied to every unrun probe a:

    VoI(a) = EIG(a) − λ·cost(a) − μ·risk(a)

  * choose:  argmax_a VoI(a)
  * stop:    max_a VoI(a) ≤ 0   ("no probe is worth what it costs")

EIG is in nats (ic/live/beliefs.py); cost is the template's relative cost;
risk is 0 for read-only probes (it matters for interventions — Gap 2). λ is
the price of one unit of probe cost in nats: the single knob the paper sweeps.
A probe budget is a safety cap, not part of the decision rule.

Selection policies — the experiment's conditions C, D and E:
  * "voi"        argmax VoI, stop when max VoI ≤ 0                (condition C)
  * "random"     uniform among probes with VoI > 0, same stop rule (condition D)
  * "exhaustive" every probe in a fixed order, no stopping rule    (condition E)

`stop_confidence` (off by default) adds a posterior threshold for sensitivity
analyses only.
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
    eig: float           # expected information gain at the moment it was chosen
    posteriors: dict[str, float]
    free: bool = False   # read from discovery output, not a probe the agent paid for
    raw: str = ""        # snippet of the raw reading, for error analysis
    eps: Optional[float] = None   # measurement error used for this reading (None: template default)
    sensor: Optional[dict] = None  # how the label was produced, when not by the regex


class Investigation:
    def __init__(self, hypotheses: list[BenchHypothesis], probes: dict[str, ProbeInstance], *,
                 policy: str = "voi", lam: float = 0.05, mu: float = 1.0,
                 stop_confidence: Optional[float] = None,
                 max_probes: int = 25, eps: Optional[float] = None, seed: int = 0):
        if policy not in POLICIES:
            raise ValueError(f"unknown policy {policy!r}")
        self.hyps = {h.id: h for h in hypotheses}
        self.probes = probes
        self.policy = policy
        self.lam = lam
        self.mu = mu
        self.stop_confidence = stop_confidence
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
        if self.stop_confidence is not None and max(self.posteriors.values()) >= self.stop_confidence:
            self.stop_reason = "confident"
            return None
        scored = {k: self.voi(k) for k in candidates}
        worth_it = [k for k, v in scored.items() if v > 0]
        if not worth_it:
            self.stop_reason = "no_probe_worth_its_cost"
            return None
        if self.policy == "random":
            return self.rng.choice(worth_it)
        return max(worth_it, key=scored.get)

    def voi(self, key: str) -> float:
        """VoI(a) = EIG(a) − λ·cost(a) − μ·risk(a), in nats."""
        p = self.probes[key]
        return self.eig(key) - self.lam * p.cost - self.mu * p.template.risk

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
        # Only EXPLICIT predictions count. The implied "other services look healthy"
        # default is a soft assumption: a knock-on symptom on a caller (frontend logging
        # connection errors because geo is down) must not discard the whole list. In the
        # pilot it triggered a needless re-generation call.
        for h in self.hyps.values():
            if all(h.predictions.get(s.probe) in (None, s.outcome) for s in self.steps):
                return False
        return True

    def summary(self) -> dict:
        return {
            "policy": self.policy,
            "probes_run": self.paid_steps(),
            "free_observations": len(self.steps) - self.paid_steps(),
            "probe_cost": sum(self.probes[s.probe].cost for s in self.steps if not s.free),
            "stop_reason": self.stop_reason,
            "lambda": self.lam,
            "leader": self.leader().id,
            "final_confidence": max(self.posteriors.values()),
            "posteriors": self.posteriors,
            "trace": [s.__dict__ for s in self.steps],
        }
