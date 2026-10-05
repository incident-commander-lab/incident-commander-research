"""RQ2 incident simulator: observe, act, escalate or stop, with known ground truth.

Built from Incident Commander's live scenarios (mock/shop_svc.py) so the causes,
checks and actions match the real system, but each episode runs in microseconds,
so policies can be compared on thousands of PAIRED incidents.

The world
  Every incident has the same opening picture: checkout errors are up and a
  deploy landed shortly before. The true cause differs:
    deploy   the new release is broken           → rollback fixes it
    dep      the inventory dependency is down    → inventory fallback fixes it
    db       the orders database is saturated    → needs a human (the only automatic
                                                    fix, a DB failover, is irreversible
                                                    and policy-denied)
    unknown  a cause outside every hypothesis    → needs a human
  'deploy' and 'dep' are the pair where acting is right (with the RIGHT action);
  'db' and 'unknown' are where holding back and escalating is right.

Costs (one currency)
  While the incident is open, every second costs `impact` units, so time spent
  checking, acting or waiting for a human scales with customer impact. Two costs
  do NOT scale with impact, and they are what makes the right decision shift
  with it: a wrong action's fixed harm (reverting good code, side effects) and
  the fixed cost of paging a human. A wrong reversible action also adds `harm`
  seconds of extra disruption.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

CAUSES = ("deploy", "dep", "db", "unknown")
HYPOTHESES = ("deploy", "dep", "db")          # what the agent can name; 'unknown' is outside


@dataclass(frozen=True)
class Check:
    name: str
    seconds: float
    outcomes: tuple[str, ...]
    truth: dict                                 # cause → outcome the check shows when it reads correctly
    eps: float                                  # chance of a wrong reading


CHECKS: dict[str, Check] = {c.name: c for c in (
    Check("version_cohorts", 20, ("new_version_worse", "versions_equal"),
          {"deploy": "new_version_worse", "dep": "versions_equal", "db": "versions_equal", "unknown": "versions_equal"}, 0.10),
    Check("db_health", 10, ("degraded", "healthy"),
          {"deploy": "healthy", "dep": "healthy", "db": "degraded", "unknown": "healthy"}, 0.10),
    Check("inventory_health", 10, ("degraded", "healthy"),
          {"deploy": "healthy", "dep": "degraded", "db": "healthy", "unknown": "healthy"}, 0.10),
    Check("error_logs", 30, ("exceptions", "timeouts", "connection_refused", "mixed"),
          {"deploy": "exceptions", "dep": "connection_refused", "db": "timeouts", "unknown": "mixed"}, 0.25),
)}


@dataclass(frozen=True)
class Action:
    name: str
    seconds: float          # time to execute and verify
    fixes: str              # the cause it fixes
    reversible: bool
    harm: float             # extra seconds of disruption when it does not fix (scales with impact)
    harm_fixed: float       # fixed cost of a wrong action (reverting good code, side effects); does NOT scale


ACTIONS: dict[str, Action] = {a.name: a for a in (
    Action("rollback", 40, "deploy", True, 60, 400),
    Action("inventory_fallback", 30, "dep", True, 30, 200),
    Action("db_failover", 60, "db", False, 600, 5000),   # irreversible: the policy layer never allows it
)}
HUMAN_DELAY = 600.0          # seconds for a paged human to resolve any cause
PAGING_COST = 300.0          # fixed cost of paging a human (does not scale with impact)


def policy_allows(action: Action) -> bool:
    """The OPA rule from the live system: irreversible actions are never autonomous."""
    return action.reversible


@dataclass
class Episode:
    cause: str
    impact: float                               # cost per second of open incident
    rng: random.Random
    elapsed: float = 0.0
    cost: float = 0.0
    resolved_by: Optional[str] = None           # "agent:<action>" | "human"
    log: list = field(default_factory=list)
    eps_scale: float = 1.0                      # sensitivity: real check noise vs the nominal noise the agent assumes
    harmful: int = 0                            # actions that did not fix (unnecessary disruption)
    denied: int = 0                             # actions the policy refused

    @property
    def open(self) -> bool:
        return self.resolved_by is None

    def _spend(self, seconds: float) -> None:
        self.elapsed += seconds
        self.cost += seconds * self.impact

    def check(self, name: str) -> str:
        c = CHECKS[name]
        self._spend(c.seconds)
        right = c.truth[self.cause]
        if self.rng.random() < min(0.9, c.eps * self.eps_scale):
            out = self.rng.choice([o for o in c.outcomes if o != right])
        else:
            out = right
        self.log.append(("check", name, out))
        return out

    def act(self, name: str) -> bool:
        a = ACTIONS[name]
        if not policy_allows(a):
            self.denied += 1
            self.log.append(("denied", name, None))
            return False
        self._spend(a.seconds)
        fixed = a.fixes == self.cause
        if fixed:
            self.resolved_by = f"agent:{name}"
        else:
            self.harmful += 1
            self._spend(a.harm)
            self.cost += a.harm_fixed
        self.log.append(("act", name, "recovered" if fixed else "no_recovery"))
        return fixed

    def escalate(self) -> None:
        self._spend(HUMAN_DELAY)
        self.cost += PAGING_COST
        self.resolved_by = "human"
        self.log.append(("escalate", None, None))

    @property
    def time_to_mitigation(self) -> float:
        return self.elapsed


def correct_handling(ep: Episode) -> bool:
    """Paired-incident correctness (after AgentAbstain): act when an autonomous fix
    exists and use the right one with no unnecessary action; otherwise hold back and
    escalate with no harmful action."""
    if ep.cause in ("deploy", "dep"):
        return ep.resolved_by == f"agent:{ {'deploy': 'rollback', 'dep': 'inventory_fallback'}[ep.cause] }" and ep.harmful == 0
    return ep.resolved_by == "human" and ep.harmful == 0
