"""Offline tests for the RQ2 simulator and decision policies."""
import random

import ic.rq2.policies as P
from ic.rq2.sim import ACTIONS, CAUSES, Episode, correct_handling, policy_allows


def ep(cause, r=1.0, seed=0):
    return Episode(cause, r, random.Random(seed))


def test_policy_gate_never_allows_irreversible_actions():
    assert not policy_allows(ACTIONS["db_failover"])
    assert policy_allows(ACTIONS["rollback"]) and policy_allows(ACTIONS["inventory_fallback"])
    for c in CAUSES:
        for pol in P.POLICIES:
            e = P.run_episode(ep(c, 4.0, 1), pol)
            assert all(kind != "act" or name != "db_failover" for kind, name, _ in e.log)


def test_every_episode_ends_resolved():
    for c in CAUSES:
        for pol in P.POLICIES:
            assert not P.run_episode(ep(c, 1.0, 2), pol).open


def test_rollback_fixes_only_a_bad_deploy_and_failure_is_evidence():
    assert ep("deploy").act("rollback")
    e = ep("db")
    assert not e.act("rollback") and e.harmful == 1 and e.cost > 0
    b = P.after_failed_action(P.prior(), "rollback")
    assert b["deploy"] < 0.01                      # a rollback that did not recover rules the deploy out


def test_confidence_needed_to_act_falls_as_customer_impact_rises():
    """The RQ2 decision boundary. A wrong rollback has a fixed harm; waiting costs
    impact per second. So the confidence needed before rolling back falls as
    impact rises, which no fixed confidence threshold can do."""
    def first(pd, r):
        rest = (1 - pd - 0.05) / 2
        b = {"deploy": pd, "dep": rest, "db": rest, "other": 0.05}
        return P.plan(b, r, frozenset(), frozenset(), {})[1]

    def threshold(r):   # lowest deploy belief at which the first move is a rollback
        return next((pd for pd in (0.6, 0.75, 0.85, 0.95) if first(pd, r) == ("act", "rollback")), 1.0)
    t = [threshold(r) for r in (0.25, 1, 4, 64)]
    assert t == sorted(t, reverse=True) and t[0] > t[-1], t
    assert first(0.85, 0.25)[0] == "check" and first(0.85, 4.0) == ("act", "rollback")


def test_escalates_when_nothing_autonomous_can_fix_it():
    b = {"deploy": 0.02, "dep": 0.02, "db": 0.90, "other": 0.06}   # database saturation: human-only fix
    assert P.plan(b, 4.0, frozenset(P.CHECKS), frozenset(), {})[1] == ("escalate", None)


def test_correct_handling_definition():
    e = ep("deploy"); e.act("rollback"); assert correct_handling(e)
    e = ep("db"); e.escalate(); assert correct_handling(e)
    e = ep("db"); e.act("rollback"); e.escalate(); assert not correct_handling(e)   # unnecessary action
