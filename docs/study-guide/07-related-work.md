# 7. Related work and competitors

Our Research Radar ([research-radar-phi.vercel.app](https://research-radar-phi.vercel.app)) keeps this list fresh every day (chapter 8).

## The idea we build on (credit it)

| Work | What it did | What it lacked |
|---|---|---|
| Heckerman, Breese & Rommelse (CACM 1995); Breese & Heckerman (UAI 1996) | Decision-theoretic troubleshooting: pick the next observation, repair or experiment by expected cost | A hand-built Bayesian model per system |
| Rish et al. (2005) | Active probing in distributed systems: pick the most informative probe | Same: a fixed model |
| Langseth & Jensen (2003) | Troubleshooting with dependent actions | Same |

**Our contribution:** the LLM builds the hypothesis model at runtime, and the classical rule decides. Plus reversibility and customer impact in the utility, tested against LLM-directed agents.

## Closest recent papers

| Work | Close to | Difference from ours |
|---|---|---|
| **GIRA** (Patel, ICLR'26 workshop) | RQ2: utility-based action gate | No information term, no beliefs, mock planner. It's our RQ2 baseline |
| **HERO** (ASE 2026) | RQ1: hypotheses + beliefs + evidence chosen by ambiguity, on OpenRCA | Closest to RQ1. Need the full PDF; must be cited and compared |
| **Wang et al.** (AISNS 2025) | Escalate when LLM confidence < 0.7 | Our conf70 baseline |
| **CIPHER-A** (Discover AI) | LLM predicts outcomes before acting | Security domain |
| **BudgetSRE** | Cost-aware SRE agent | Budget, not decision theory over beliefs |
| **RACER** | Evidence-gated, risk-bounded autonomous recovery | Simulation only |
| **Model Discovery Agent** | LLM proposes + VoI decides | Science, not operations |
| **OpenReliOps, Incident-Arena, AI SRE Arena** | Benchmarks | Candidate held-out benchmarks for confirming rule v2 |
| **ITBench** (IBM) | Benchmark | Published SRE success 11.4% (the preprint figures differ) |

## Companies

| Company | What it claims | Note |
|---|---|---|
| **Cleric** | AI SRE; its white paper (May 2026) claims "graduated autonomy by problem type", learned from verified outcomes | No method or numbers published; reviews say the shipped product is read-only investigation |
| **Resolve AI** | Maximum autonomy | — |
| **Traversal** | Root-cause analysis; low-confidence findings go to a human | — |
| **Microsoft Agent Governance Toolkit** (open source, Apr 2026) | OPA policies, dynamic trust scores, rollback, kill switch | Close to our safety layer |
| **Gremlin** | Disaster-recovery tests, AI re-testing after fixes | Chaos engineering, not diagnosis |

## Frameworks people cite

- **CSA**, "Leveling Up Autonomy in Agentic AI" (Jim Reavis, blog, 28 Jan 2026): L0–L5 autonomy levels. It lists *dynamic autonomy adjustment* and *verification* as **open questions**, which is good support for our direction. Don't cite the "CSA Labs" PDFs; they're AI-generated and unreviewed.
- **Gartner:** use the June 2025 "40% of agentic AI projects will be cancelled" figure. The "40% will demote agents" claim has only second-hand sources.

## So what is actually new about ours?

Be precise. These are **not** new: "AI SRE agent", "LLM + Bayesian beliefs", "graduated autonomy", "policy gate".

What we can defend:
1. **A fixed expected-utility rule, not the LLM, chooses checks, stopping and act-or-escalate**, over LLM-written hypotheses, **measured** against LLM-directed agents on a public benchmark with paired tests.
2. **Same accuracy as an LLM choosing, at a third of the tokens**, and much better than a plain agent; plus a documented failure (the 14B collapse) and its fix.
3. **Open and reproducible:** the code, the replay tool and the audit, in contrast with the closed vendors.
