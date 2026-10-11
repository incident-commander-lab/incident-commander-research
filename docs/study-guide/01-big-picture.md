# 1. The big picture

## The problem

When something breaks in a cloud system (a website is slow, payments fail), an engineer on call has to answer three questions fast:

1. **Is something actually broken?** (detection)
2. **Where?** Which service? (localization)
3. **What should we do?** Fix it, wait and look more, or call someone senior? (act or escalate)

Companies now try AI agents for this. The usual design is "let the LLM decide everything": what to look at, when it knows enough, and whether to act. That has known problems:
- It **over-investigates**: it repeats the same checks and burns tokens.
- It **stops at the wrong time**: it is confident and wrong.
- It **acts when it shouldn't**.

## Our idea in one sentence

> **The LLM suggests possible causes; a fixed, transparent rule decides what to check next, when to stop, and whether to act or escalate.**

The LLM is good at reading logs and imagining causes. It is bad at disciplined decisions. A 30-year-old idea from decision theory (Heckerman, Breese and others, 1990s) is good at decisions, but used to need a human to hand-build the model of every system. We combine them: **the LLM builds the model on the fly, and the old rule decides.**

## The research questions

| | Question | Where | Status |
|---|---|---|---|
| **RQ1** | Does the rule choose checks and stop better than the LLM does? | Microsoft's AIOpsLab benchmark, 73 Kubernetes incidents | Main runs done; re-runs after the bug fix in progress |
| **RQ2** | Does the rule decide well between fix, wait and escalate? | Our incident simulator | Done |
| **RQ3** | Does it hold up on a real running system? | A live app fed by real public data streams | Planned (chapter 9) |

## Glossary

| Word | Meaning |
|---|---|
| **Incident** | Something is broken or degraded in production |
| **SRE** | Site Reliability Engineer: the people on call |
| **Hypothesis** | A possible cause, e.g. "the geo service crashed" |
| **Probe / check** | One read-only look at the system, e.g. "are geo's pods healthy?" (`kubectl get pods`) |
| **Belief / posterior** | How likely each hypothesis is right now, updated after every check |
| **VoI (value of information)** | How much a check is expected to reduce our uncertainty, minus what it costs |
| **EIG** | Expected information gain: the "how much will we learn" part of VoI |
| **λ (lambda)** | How expensive checks are relative to information. Higher λ means stop sooner |
| **Catch-all hypothesis** | "Something else is wrong with service X": a safety net for causes the LLM didn't think of (rule v2) |
| **ReAct agent** | The standard LLM agent: think, act, look, repeat, with the LLM deciding everything |
| **OPA / Rego** | Open Policy Agent: a separate program that says ALLOW or DENY to every action |
| **Escalate** | Hand the incident to a human |
| **Reversible action** | Can be undone safely (rollback, small canary); irreversible ones (database failover) never run on their own |
| **Wilson interval / 95% range** | The honest error bar on a percentage from a small sample |
| **McNemar test** | Compares two setups on the *same* problems: counts problems only A solved vs only B solved |
| **qwen3:8b / 14b** | The two open LLMs we use, running on the laptop with Ollama (8 and 14 billion parameters) |
