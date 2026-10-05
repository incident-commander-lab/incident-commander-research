"""RQ2 policies: who decides between observing, acting and escalating.

Every policy sees the same belief machinery (Bayesian update over the agent's
hypotheses, including an explicit 'other' hypothesis for causes outside the
list) and the same policy gate. They differ only in the decision rule.

  ours        expected outage cost (full lookahead), one currency for everything: a check is worth
              running if what it reveals saves more outage than the time it takes;
              an action is valued for what it fixes AND what its outcome reveals
              (rollback that does not recover rules the deploy out); escalation
              costs the human delay. Choose the option with the lowest expected cost.
  ours_myopic the same rule with one-step lookahead only (ablation).
  gira        GIRA-style utility (Patel, ICLR 2026 workshop): act on the best
              intervention if E[impact reduction] − λ·E[risk] > 0; otherwise observe
              (fixed order, no information value); escalate when nothing is left.
  conf70      act on the leading hypothesis once its confidence ≥ 0.7, else keep
              checking (most informative first); escalate when checks run out
              (the confidence-threshold rule of Wang et al. 2025).
  diag_first  diagnose to ≥ 0.9 confidence first, then act; escalate otherwise.
"""
from __future__ import annotations

import math
from typing import Optional

from .sim import ACTIONS, CHECKS, HUMAN_DELAY, PAGING_COST, Episode, policy_allows

AGENT_HYPS = ("deploy", "dep", "db", "other")
FIX_FOR = {a.fixes: a.name for a in ACTIONS.values()}          # cause → action that fixes it
OTHER_PRIOR = 0.10


# --------------------------------------------------------------------------- #
# Beliefs
# --------------------------------------------------------------------------- #
def prior() -> dict[str, float]:
    rest = (1 - OTHER_PRIOR) / 3
    return {"deploy": rest, "dep": rest, "db": rest, "other": OTHER_PRIOR}


def lik_check(h: str, check: str, outcome: str) -> float:
    c = CHECKS[check]
    if h == "other":                                            # agnostic: any reading equally likely
        return 1.0 / len(c.outcomes)
    return 1 - c.eps if c.truth[h] == outcome else c.eps / (len(c.outcomes) - 1)


def update(b: dict[str, float], lik) -> dict[str, float]:
    raw = {h: p * lik(h) for h, p in b.items()}
    z = sum(raw.values())
    return {h: v / z for h, v in raw.items()} if z > 0 else dict(b)


def after_check(b, check, outcome):
    return update(b, lambda h: lik_check(h, check, outcome))


def after_failed_action(b, action):
    """An action that did not fix rules its target cause (almost) out."""
    fixes = ACTIONS[action].fixes
    return update(b, lambda h: 0.02 if h == fixes else 1.0)


def predictive(b, check) -> dict[str, float]:
    return {y: sum(p * lik_check(h, check, y) for h, p in b.items()) for y in CHECKS[check].outcomes}


def entropy(b) -> float:
    return -sum(p * math.log(p) for p in b.values() if p > 0)


def eig(b, check) -> float:
    pred = predictive(b, check)
    return max(0.0, entropy(b) - sum(py * entropy(after_check(b, check, y)) for y, py in pred.items() if py > 0))


# --------------------------------------------------------------------------- #
# Expected outage cost (ours)
# --------------------------------------------------------------------------- #
def escalate_cost(r: float) -> float:
    return HUMAN_DELAY * r + PAGING_COST


def act_value(b, action, r, tried: frozenset) -> float:
    """Expected cost of trying `action` now and then continuing optimally among
    actions and escalation (no more checks): it fixes with probability P(its cause);
    if it fails, the failure itself is evidence (the cause is ruled out)."""
    a = ACTIONS[action]
    p = b.get(a.fixes, 0.0)
    fail_b = after_failed_action(b, action)
    return a.seconds * r + (1 - p) * (a.harm * r + a.harm_fixed + terminal_value(fail_b, r, tried | {action}))


def terminal_value(b, r, tried: frozenset = frozenset()) -> float:
    """Best expected cost using only actions and escalation from belief b."""
    best = escalate_cost(r)
    for name, a in ACTIONS.items():
        if name in tried or not policy_allows(a):
            continue
        best = min(best, act_value(b, name, r, tried))
    return best


def check_value(b, check, r, tried) -> float:
    """One-step lookahead (kept for the 'ours_myopic' ablation)."""
    return CHECKS[check].seconds * r + sum(
        py * terminal_value(after_check(b, check, y), r, tried) for y, py in predictive(b, check).items() if py > 0)


def choose_myopic(b, r, ran: set, tried: frozenset):
    options = [("escalate", None, escalate_cost(r))]
    for name, a in ACTIONS.items():
        if name not in tried and policy_allows(a):
            options.append(("act", name, act_value(b, name, r, tried)))
    for c in CHECKS:
        if c not in ran:
            options.append(("check", c, check_value(b, c, r, tried)))
    return min(options, key=lambda o: o[2])[:2]


def _key(b):
    return tuple(round(b[h], 4) for h in AGENT_HYPS)


def plan(b, r, ran: frozenset, tried: frozenset, memo: dict):
    """Exact expected-cost plan over every remaining check and action (the world is
    small enough to search fully). Returns (expected cost, first move). A failed
    action is evidence, so acting can be worth it partly for what it reveals."""
    key = (_key(b), ran, tried, r)
    if key in memo:
        return memo[key]
    best = (escalate_cost(r), ("escalate", None))
    for name, a in ACTIONS.items():
        if name in tried or not policy_allows(a):
            continue
        p = b.get(a.fixes, 0.0)
        cont = plan(after_failed_action(b, name), r, ran, tried | {name}, memo)[0] if p < 1 else 0.0
        v = a.seconds * r + (1 - p) * (a.harm * r + a.harm_fixed + cont)
        if v < best[0]:
            best = (v, ("act", name))
    for c in CHECKS:
        if c in ran:
            continue
        v = CHECKS[c].seconds * r + sum(py * plan(after_check(b, c, y), r, ran | {c}, tried, memo)[0]
                                        for y, py in predictive(b, c).items() if py > 0)
        if v < best[0]:
            best = (v, ("check", c))
    memo[key] = best
    return best


_MEMO: dict = {}


def choose_ours(b, r, ran: set, tried: frozenset):
    return plan(b, r, frozenset(ran), tried, _MEMO)[1]


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #
GIRA_LAMBDA = 1.0


def choose_gira(b, r, ran, tried):
    best, best_u = None, 0.0
    for name, a in ACTIONS.items():
        if name in tried or not policy_allows(a):
            continue
        p = b.get(a.fixes, 0.0)
        u = p * HUMAN_DELAY * r - GIRA_LAMBDA * (1 - p) * (a.harm * r + a.harm_fixed)   # impact avoided vs waiting for a human
        if u > best_u:
            best, best_u = name, u
    if best:
        return "act", best
    for c in sorted(CHECKS, key=lambda c: CHECKS[c].seconds):            # fixed order: no information value
        if c not in ran:
            return "check", c
    return "escalate", None


def _threshold_policy(threshold):
    def choose(b, r, ran, tried):
        lead = max(b, key=b.get)
        if b[lead] >= threshold:
            action = FIX_FOR.get(lead)
            if action and action not in tried and policy_allows(ACTIONS[action]):
                return "act", action
            return "escalate", None
        unrun = [c for c in CHECKS if c not in ran]
        if not unrun:
            return "escalate", None
        return "check", max(unrun, key=lambda c: eig(b, c))
    return choose


POLICIES = {"ours": choose_ours, "ours_myopic": choose_myopic, "gira": choose_gira,
            "conf70": _threshold_policy(0.7), "diag_first": _threshold_policy(0.9)}


def run_episode(ep: Episode, policy: str, max_steps: int = 12) -> Episode:
    choose = POLICIES[policy]
    b, ran, tried = prior(), set(), frozenset()
    for _ in range(max_steps):
        if not ep.open:
            break
        kind, arg = choose(b, ep.impact, ran, tried)
        if kind == "check":
            ran.add(arg)
            b = after_check(b, arg, ep.check(arg))
        elif kind == "act":
            tried = tried | {arg}
            if not ep.act(arg):
                b = after_failed_action(b, arg)
        else:
            ep.escalate()
    if ep.open:
        ep.escalate()
    return ep
