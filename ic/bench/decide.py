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
the price of one unit of probe cost in nats (default 0.02, lowered from 0.05 after the pilot stopped early 50% of the time): the single knob the paper sweeps.
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

# Calibration defaults, set after the qwen3:8b pilot (overconfident: Brier 0.35, 98%
# confidence on wrong answers; a no-fault incident was flagged as faulty):
NO_FAULT_PRIOR = 0.25   # fixed starting share of the no-fault hypothesis
PRIOR_TEMPER = 0.5      # LLM confidences blended halfway toward uniform
PREDICTION_EPS = 0.10   # chance the LLM's predicted probe outcome is itself wrong
# Rule v2, after the qwen3:14b run (detection fell to 10/32; 22 misses ended at "no fault",
# 15 of them after seeing an abnormal reading). v1 reproduces the original rule.
OTHER_PRIOR = 0.10      # fixed share of the catch-all "some other fault in <service>" hypotheses
RULE_VERSION = 2


def likelihood(prediction: Optional[str], outcome: str, outcomes: tuple[str, ...],
               eps: float, miss: Optional[float] = None) -> float:
    """P(outcome | hypothesis). Same as live mode's model, plus one asymmetry from
    classical diagnosis: a predicted symptom that does not show up (the reading is
    the healthy label) has probability `miss`, not ε/(k−1). Faults are often silent
    in logs; absence of a symptom is weak evidence, presence is strong."""
    k = len(outcomes)
    if prediction is None:
        return 1.0 / k
    if prediction == outcome:
        return 1.0 - eps
    healthy = outcomes[-1]
    if miss is not None and outcome == healthy and prediction != healthy:
        return miss
    return eps / (k - 1)


def initial_posteriors(hypotheses: list[BenchHypothesis], no_fault_prior: float = NO_FAULT_PRIOR,
                       prior_temper: float = PRIOR_TEMPER, other_prior: float = OTHER_PRIOR
                       ) -> dict[str, float]:
    """No-fault gets a fixed share; fault hypotheses split the rest by LLM confidence
    tempered toward uniform. Raw LLM confidences (six causes at 0.5–0.8 against 0.1
    for no-fault) started no-fault at 2.7% in the pilot. Catch-all hypotheses, when
    present, share a fixed `other_prior` evenly."""
    faults = [h for h in hypotheses if h.service is not None and not h.catch_all]
    nofault = [h for h in hypotheses if h.service is None]
    others = [h for h in hypotheses if h.catch_all]
    if not faults and not others:
        return {h.id: 1.0 / len(hypotheses) for h in hypotheses}
    o = other_prior if others else 0.0
    mass = 1.0 - (no_fault_prior if nofault else 0.0) - (o if faults else 0.0)
    post: dict[str, float] = {}
    if faults:
        total = sum(max(h.prior, 1e-6) for h in faults)
        post = {h.id: mass * ((1 - prior_temper) * max(h.prior, 1e-6) / total + prior_temper / len(faults))
                for h in faults}
    for h in others:
        post[h.id] = (o if faults else mass) / len(others)
    for h in nofault:
        post[h.id] = no_fault_prior / len(nofault)
    return post


@dataclass
class BenchHypothesis:
    id: str
    service: Optional[str]          # None ⇒ the "no fault" hypothesis
    fault_type: str                 # one of AIOpsLab's analysis categories, or "None"
    system_level: str
    claim: str
    prior: float
    predictions: dict[str, str] = field(default_factory=dict)  # probe key → outcome
    catch_all: bool = False         # "some other fault in `service`": no symptom predictions


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
                 policy: str = "voi", lam: float = 0.02, mu: float = 1.0,
                 stop_confidence: Optional[float] = None,
                 max_probes: int = 25, eps: Optional[float] = None, seed: int = 0,
                 no_fault_prior: float = NO_FAULT_PRIOR, prior_temper: float = PRIOR_TEMPER,
                 prediction_eps: float = PREDICTION_EPS, other_prior: float = OTHER_PRIOR,
                 rule_version: int = RULE_VERSION):
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
        self.prediction_eps = prediction_eps
        self.rule_version = rule_version
        self.posteriors = initial_posteriors(hypotheses, no_fault_prior, prior_temper, other_prior)
        self.steps: list[Step] = []
        self.unavailable: list[str] = []  # probes whose tool failed: paid for, no evidence
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

    def _h_eps(self, hid: str, key: str, sensor: float) -> float:
        """Error for one hypothesis's prediction of one probe. v2: the LLM's own error
        (prediction_eps) applies only to outcomes the LLM actually predicted. The implied
        "other services look healthy" and the no-fault hypothesis are definitions, not
        guesses, so they carry only the probe's error. v1 gave "no fault" the LLM's error
        margin too, so a visibly abnormal reading counted only about a third as strongly
        against it."""
        h = self.hyps[hid]
        if self.rule_version >= 2 and (h.service is None or key not in h.predictions):
            return sensor
        return sensor + (1 - sensor) * self.prediction_eps

    def eig(self, key: str) -> float:
        probe = self.probes[key]
        preds, e, miss = self._predictions(key), self._sensor_eps(probe), probe.template.miss
        eps = {h: self._h_eps(h, key, e) for h in self.posteriors}
        prior_h = beliefs.entropy(self.posteriors)
        expected = 0.0
        for y in probe.outcomes:
            joint = {h: p * likelihood(preds[h], y, probe.outcomes, eps[h], miss) for h, p in self.posteriors.items()}
            p_y = sum(joint.values())
            if p_y > 0:
                expected += p_y * beliefs.entropy({h: v / p_y for h, v in joint.items()})
        return max(0.0, prior_h - expected)

    def _update(self, key: str, outcome: str, sensor: float) -> dict[str, float]:
        probe = self.probes[key]
        preds = self._predictions(key)
        raw = {h: p * likelihood(preds[h], outcome, probe.outcomes, self._h_eps(h, key, sensor),
                                 probe.template.miss)
               for h, p in self.posteriors.items()}
        z = sum(raw.values())
        return {h: v / z for h, v in raw.items()} if z > 0 else dict(self.posteriors)

    def _sensor_eps(self, probe: ProbeInstance, sensor_eps: Optional[float] = None) -> float:
        """The probe's own chance of a wrong reading (per-hypothesis prediction error is
        added in _h_eps)."""
        return sensor_eps if sensor_eps is not None else (probe.template.eps if self.eps is None else self.eps)

    def _eps(self, probe: ProbeInstance, sensor_eps: Optional[float] = None) -> float:
        """Total chance a reading contradicts a TRUE LLM-predicted hypothesis."""
        e = self._sensor_eps(probe, sensor_eps)
        return e + (1 - e) * self.prediction_eps

    def _unrun(self) -> list[str]:
        done = {s.probe for s in self.steps} | set(self.unavailable)
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
        self.posteriors = self._update(key, outcome, self._sensor_eps(probe, eps))
        self.steps.append(Step(key, outcome, eig, dict(self.posteriors), free, raw[:600], eps, sensor))

    def mark_unavailable(self, key: str) -> None:
        """The probe ran but its tool could not read the service: spend the step,
        update nothing."""
        self.unavailable.append(key)

    def paid_steps(self) -> int:
        return sum(1 for s in self.steps if not s.free) + len(self.unavailable)

    # ------------------------------------------------------------------ #
    def leader(self) -> BenchHypothesis:
        return self.hyps[max(self.posteriors, key=self.posteriors.get)]

    def service_marginals(self) -> dict[str, float]:
        """P(the fault is in service s), summed over every hypothesis naming s."""
        out: dict[str, float] = {}
        for hid, p in self.posteriors.items():
            svc = self.hyps[hid].service
            if svc is not None:
                out[svc] = out.get(svc, 0.0) + p
        return out

    def p_fault(self) -> float:
        """P(something is broken) = 1 − P(no fault): the answer to a detection question."""
        return 1.0 - sum(p for h, p in self.posteriors.items() if self.hyps[h].service is None)

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
            if h.catch_all:
                continue  # predicts nothing, so it can never be contradicted
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
            "unavailable_probes": list(self.unavailable),
            "lambda": self.lam,
            "leader": self.leader().id,
            "final_confidence": max(self.posteriors.values()),
            "p_fault": self.p_fault(),
            "rule_version": self.rule_version,
            "posteriors": self.posteriors,
            "trace": [s.__dict__ for s in self.steps],
        }
