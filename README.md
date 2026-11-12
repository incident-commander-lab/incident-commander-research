<div align="center">

# Incident Commander: research repo

**An AI SRE agent that decides when *not* to act: which checks to run, when to stop, and when to hand an incident to a human.**

Private research repo of Shahesta Salama and Nourseen Tarek (Alryada University). The public demo lives at [incident-commander-lab/incident-commander](https://github.com/incident-commander-lab/incident-commander).

[Status](#status-15-oct-2026) · [Results](#rq1-results-final) · [Start here](#start-here-reading-order) · [Layout](#repository-layout) · [How to run](#how-to-run-things) · [Live test bed](#rq3-the-live-test-bed-pulse) · [Next](#what-comes-next) · [Rules](#rules-we-keep)

</div>

---

## Status (15 Oct 2026)

| Track | What it is | Status | Owner |
|---|---|---|---|
| **RQ1: benchmark** | Rule vs LLM agents on 73 AIOpsLab problems (detection, localization, analysis) | **Done.** 730 runs, audited, final snapshot `data/rq1-results/2026-10-15/` | Shahesta (runs), Nour (evaluation) |
| **RQ2: act or escalate** | Simulator: when should the agent fix, wait or call a human, at each customer-impact level | Done | Shahesta |
| **RQ3: live system** | "Pulse": our own app on real Wikipedia traffic, unannounced faults, the agent fixes or escalates | Built and frozen (12 Oct). Missing before scored sessions: plain-LLM agent baseline, unseen faults, session runner | Both. Nour owns the protocol and scorer |
| **Paper 1** | RQ1 + RQ2 + RQ3 | Introduction drafted (`docs/paper1/`); results sections next | Both |
| **Paper 2** | The Upgrade Paradox: a smarter LLM made the rule *worse* (rule v1, 8B → 14B: 42% → 26%); rule v2 fixes it | Next experiment: the model ladder | Shahesta + Claude |

The full plan and its reasons: [`docs/study-guide/00-decision.md`](docs/study-guide/00-decision.md).

## The idea in one paragraph

An LLM is good at *proposing* causes ("the worker's new release is broken", "the database is locked") but bad at deciding how much evidence is enough. So the LLM writes competing **hypotheses**, each predicting what every check would show. A fixed **decision rule** (Bayesian updating plus value of information) then picks the check that best separates them, updates the probabilities, and **stops** when no check is worth its cost. When acting, an outside policy engine (OPA) must allow the fix, the agent verifies recovery from fresh telemetry, and reverts and escalates if the fix doesn't hold. The rule is deterministic and auditable: the same readings always give the same decision.

## RQ1 results (final)

Share of the 73 AIOpsLab problems answered correctly. qwen3 models run locally through Ollama.

| Setup | qwen3:8b | qwen3:14b | Checks per problem (8B / 14B) | Input tokens per problem (8B / 14B) |
|---|---|---|---|---|
| A. Plain LLM agent (ReAct) | 12% | 18% | 16.1 / 21.5 | 42k / 59k |
| B. LLM picks the checks | 45% | 55% | 2.2 / 0.8 | 15k / 9.7k |
| C. Rule v1 (ours) | 42% | 26% | 2.3 / 5.5 | 3.9k / 3.2k |
| **C2. Rule v2 (ours)** | **48%** | **52%** | 2.9 / 5.3 | 4.3k / 3.4k |
| D. Random checks | 40% | – | 3.2 | 4.1k |
| E. Every check | 47% | – | 25.0 | 8.4k |

> **Read this before quoting any number.** 32 of the 73 problems are *detection* ("is there a fault?") and 29 of those have one. **Always answering "Yes" scores 40% overall with zero checks.** On the 41 localization + analysis problems, every setup is between 15% and 32%, and none separates from random checks (n = 41, the 95% intervals overlap). Report accuracy **per task** and include the always-"Yes" baseline. Details and the per-task table: [`data/rq1-results/2026-10-15/README.md`](data/rq1-results/2026-10-15/README.md).

What the results support:
- **Cost.** Rule v2 matches "every check" at about 1/9 of the checks, and the plain LLM agent at about 1/10 (8B) to 1/17 (14B) of the input tokens, while being far more accurate than it.
- **The Upgrade Paradox (Paper 2).** Rule v1 got worse with the stronger model; rule v2 doesn't.
- **Not supported:** "the rule finds the root cause more accurately than an LLM choosing checks". The live system (RQ3), where we control the fault mix, is where accuracy gets tested properly.

Charts: `docs/study-guide/img/chart-*.png` (regenerated from the final results).

## Start here (reading order)

1. [`docs/study-guide/README.md`](docs/study-guide/README.md): the study guide, chapters 0–10 with screenshots. Read 0, 1, 3, 4 and 9 first.
2. [`data/rq1-results/2026-10-15/README.md`](data/rq1-results/2026-10-15/README.md): the final results and the detection caveat.
3. [`docs/paper/RQ3_PROTOCOL.md`](docs/paper/RQ3_PROTOCOL.md): how live sessions are scored (H3a–H3d), frozen before the first scored session.
4. [`testbed/README.md`](testbed/README.md): run the live system on your own machine.
5. [`docs/paper1/`](docs/paper1/): Paper 1 LaTeX (`main.tex`, `intro.tex`, `numbers.tex`, `refs.bib`).

## Repository layout

```
ic/bench/            RQ1: the agent on AIOpsLab
  probes.py            check catalog (catalog v2: alias services dropped, tool errors = unavailable)
  hypotheses.py        LLM hypothesis generation (each hypothesis predicts every check's outcome)
  decide.py            the decision rule (Investigation, VoI, stop rule; rule v1 and v2)
  agent.py             one investigation end to end (CATALOG_VERSION = 2)
  baselines.py         A plain LLM (ReAct), B LLM picks, D random, E every check
  run_aiopslab.py      runs one setup over the problems, one JSON line per run
  analyze.py           the results table (accuracy, Wilson 95% CI, checks, tokens, Brier)
  replay.py            offline replay of saved runs under another rule version
ic/eval/             Nour's offline evaluation on saved runs (no cluster, no LLM unless stated)
  calibration.py       selective prediction: accuracy when each method may abstain below a confidence
  evidence_failure.py  how answers degrade when a share of checks "fail"
  react_confidence.py  post-hoc confidence for the plain LLM agent (asks the same model)
ic/rq2/              RQ2 simulator: run.py (policies across impact levels), sweep.py (robustness)
ic/ (rest)           Phase 1 demo engine: orchestrator, OPA client, verification, evidence chain

testbed/             RQ3 live system "Pulse" (Docker Compose), see its README
  app/                 Node.js collector / worker / API (Wikimedia EventStreams → Redis → Postgres)
  ops/testbed_ops/     chaos.py (faults + sealed answer key), agent.py (frozen rule v2 + fix choice),
                       actions.py, probes.py, score.py (Nour's scorer), test_score.py
  policy/              OPA Rego for allowed actions
  prometheus/ alertmanager/ grafana/   monitoring, 9 alert rules, dashboard "Pulse test bed"
  start-live.command / stop-live.command   one-click start/stop (macOS)

data/rq1-results/    dated, checksummed result snapshots (2026-10-11, -12, -15 = final)
docs/study-guide/    the study guide + tools/make_charts.py, tools/audit_runs.py
docs/paper/          RQ3_PROTOCOL.md, EXPERIMENT_PLAN.md
docs/paper1/         Paper 1 LaTeX
infra/               AWS benchmark host (CloudFormation bench-host.yaml, bench.sh runbook, tunnel.sh)
tests/               pytest for the rule, catalog, baselines, eval tools
mock/ server/ console/ policy/ corpus/   Phase 1 demo (see the end of this file)
```

## How to run things

Setup once: Python 3.11+, then `pip install -e .` from the repo root.

**Tests** (should all pass before any PR):
```bash
python3 -m pytest -q                              # 110 tests: rule, catalog, baselines, eval
cd testbed/ops && python3 -m pytest -q            # 20 tests: the RQ3 scorer
```

**Read the RQ1 results** (no AWS, no LLM):
```bash
gunzip -k data/rq1-results/2026-10-15/full_rq1.jsonl.gz
python3 -m ic.bench.analyze data/rq1-results/2026-10-15/full_rq1.jsonl          # the table above
python3 docs/study-guide/tools/audit_runs.py <dir with full_rq1.jsonl + full_progress.txt>
python3 docs/study-guide/tools/make_charts.py <same dir>                         # rewrites docs/study-guide/img/
```

**Offline evaluation** on the final file:
```bash
python3 -m ic.eval.calibration      data/rq1-results/2026-10-15/full_rq1.jsonl.gz --out out/
python3 -m ic.eval.evidence_failure data/rq1-results/2026-10-15/full_rq1.jsonl.gz --out out/
python3 -m ic.bench.replay          ...   # re-decide saved runs under another rule version
```

**RQ2 simulator:**
```bash
python3 -m ic.rq2.run --n 2000
python3 -m ic.rq2.sweep --n 1200 --impact 4
```

**RQ1 benchmark on AWS** (Shahesta's laptop, it holds the AWS profile; the model runs on the laptop, the cluster on an AWS r7i.xlarge, joined by an SSH-over-SSM tunnel). `infra/bench.sh` is the runbook:
```bash
~/ic-bench.sh status | fetch | pause | night <n>
```
All nights are finished; the host is stopped with its disk kept (about $0.40/day). Chapter 10 of the study guide explains every command, and chapter 5 lists every problem we hit and how it was fixed.

## RQ3: the live test bed (Pulse)

A small real system: the live Wikimedia edit stream (about 10–25 events/s, real day/night cycles) → Node.js collector → Redis stream → worker → Postgres → API, with Prometheus, Alertmanager and Grafana. A fault injector breaks it at random and writes the answer to a **sealed** volume the agent can't read. Alertmanager calls the agent, the agent investigates with the frozen rule v2, and either fixes (only if OPA allows it and P(fix works) ≥ 0.78, then verifies and reverts if needed) or escalates with a one-command approve.

```bash
cd testbed && cp .env.example .env && docker compose up -d --build     # or double-click start-live.command
```

| What | Where |
|---|---|
| Grafana | http://localhost:3301 (dashboard "Pulse test bed") |
| Prometheus alerts | http://localhost:9390/alerts |
| API | http://localhost:3300/api/top |
| Agent | http://localhost:8380/incidents, decisions in `testbed/incidents/*.json` |

Faults: `bad_release_worker`, `crash_loop_worker`, `bad_config_collector`, `db_lock`, `redis_oom` (the first and `db_lock` were used in the shakedown, so they count as **seen**). Autonomy levels: `shadow`, `suggest`, `act` (`AUTONOMY` in `.env`). Scoring:
```bash
docker compose exec chaos cat /sealed/ground_truth.jsonl > ground_truth.jsonl
cd testbed/ops && python -m testbed_ops.score ../ground_truth.jsonl ../incidents
python -m testbed_ops.score compare KEY_A INCIDENTS_A KEY_B INCIDENTS_B     # paired, McNemar
```
Full guide: [`testbed/README.md`](testbed/README.md). Protocol: [`docs/paper/RQ3_PROTOCOL.md`](docs/paper/RQ3_PROTOCOL.md).

## What comes next

| When | What | Who |
|---|---|---|
| Now | **Paper 2 model ladder:** qwen3 0.6B / 1.7B / 4B / 8B / 14B write hypotheses for the 73 problems; rule v1 vs v2 replayed offline on the saved "every check" readings; scored **per task** with the always-"Yes" baseline. Laptop only, about 4–6 h | Shahesta + Claude |
| Now | RQ1 write-up with per-task numbers (the detection caveat above); calibration and evidence-failure figures from `ic/eval` on the final file | Nour |
| ~3 days | **Live benchmark, local:** a plain-LLM agent on the test bed (the comparison for H3a–H3c), 3–4 new faults the agent has never seen (H3d), a session runner. First scored session about 18–19 Oct | Shahesta + Claude; Nour locks the protocol |
| From ~26 Oct | **Mentor-scale version on AWS:** OWASP Juice Shop as a second app, Locust load tests shaped by real traffic traces, node exporter (CPU/RAM) and NVIDIA DCGM (GPU) dashboards, Kubernetes. About a week once compute is available. We now have a 4-vCPU **Spot** quota for G/VT instances in **eu-north-1 (Stockholm)**, enough for one g4dn/g5/g6.xlarge GPU machine | Both |

## Rules we keep

- **Freeze before measuring.** Rule v2 and the test-bed agent are frozen (tag `testbed-agent-frozen-1`). Any change afterwards is reported. Every result records `rule_version`, `catalog_version` and the model.
- **Audit before trusting.** Run `audit_runs.py` after every fetch. A measurement bug (two broken checks) once inflated results, and 201 runs were re-run because of it.
- **Report honestly.** Per-task accuracy with confidence intervals, trivial baselines included, and negative results stated plainly.
- **Results are snapshots.** Never edit a dated folder under `data/rq1-results/`; add a new one with its own `SHA256SUMS` and README.
- **No secrets in the repo.** Snapshots are scanned for keys, emails and personal paths before committing. `.env` files stay local.
- **Confidential work stays out.** The possible patent idea is not in this repo and must not go into decks, talks or papers until we decide about filing.

## Branches and workflow

- `main` and `research/voi-benchmark` are kept identical. Work on a branch, open a PR into `main`, and after merging, `research/voi-benchmark` is fast-forwarded to match.
- Conventional commits (`feat(eval): …`, `fix(score): …`, `data: …`, `docs: …`).
- Run both test suites before a PR.

## Phase 1 demo (the original system)

The repo still contains the Phase 1 demo: a mock `shop-svc`, an SLO detector, the investigation loop, OPA, verification with auto-revert, and a Merkle + Ed25519 sealed postmortem (`python verify.py results/<id>.postmortem.json`). Requirements: Docker Desktop, Python 3.11+, Node 18+. Start it with `./start.command` (OPA on 8181, backend on 8000, `shop-svc` on 9001, console on 5173) and stop it with `./stop.command`. On the console's **Live** tab, start traffic and trigger one of four scenarios: bad release → rollback, hidden dependency → auto-revert, two causes → abstain, database saturation → OPA deny. Study guide chapter 2 walks through it with screenshots.

## License and authors

MIT, see `LICENSE`. Shahesta Salama · Nourseen Tarek · Alryada University.

## Updates

Our shared log: open the repo, read the newest entry, and you know where we stand and who does
what next.

**How to add an entry** (every time either of us finishes something):
- Put it at the **top** of this list, dated, with your name.
- **Done:** what you finished, with the PR or commit.
- **Next:** the next task for each of us, concrete enough to start without asking.
- **Blocked on:** anything waiting for the other person or for something outside.
- If the overall plan changed, update "Remaining overall" below the entries.

### 3 Nov 2026, Nour

**Done** (PR #8, branch `eval/rq3-recordings`):
- The six seed recordings (28 Oct): windows, event counts, max gaps and SHA-256 in
  `RQ3_PROTOCOL.md`. Rebuilt on the session host from the bucket: all six SHA-256 match.
- Smoke sessions passed (3 Nov, Shahesta's on-demand g4dn): rule and LLM agent, 2 seen faults
  each, 0 inject failures, headers and reports complete.
- Test bed fixes found by the laptop attempts (1 Nov): clean state at every session start, the
  injector stays up after its last slot, the answer key is always copied, atomic release
  files, and `run-session.sh` on Windows.
- Scorer: every incident on a fault now counts for H3a/H3b, the fault staying the unit
  (Shahesta's proposal, accepted in "Changes"). `testbed/sessions/` is ignored by git.

- Shahesta's second review: the cooldown hole is closed (both agents), `run-session.sh`
  refuses to run without a git commit and `analyze` rejects a header without one. Because this
  changes the agent, the smoke sessions are re-run on the final commit.

- Final smoke sessions on `a3131dc` passed (3 Nov): both headers at `a3131dc`, clean tree,
  all six seeds match, cooldown held, no harmful actions. Rule 1/2 resolved, LLM 2/2; the rule
  agent's db_lock outcome differed from the earlier run (noted in "Changes").

**Next, Shahesta:**
1. Merge PR #8 (merge commit) and tag `rq3-protocol-locked` on `a3131dc` itself, not on the
   branch head: the commits after it change documentation only.
2. Then the pilot host, a `git clone` at the tag (about 9 h for two sessions).

**Next, Nour:**
1. The pilot: seeds 101–102, both agents (A-B-B-A), and the disagreement rate.
2. Meanwhile: the RQ2 section of Paper 1.

**Blocked on:** the merge and the tag.

### 19 Oct 2026, Nour

**Done** (PR #5, branch `eval/rq3-protocol-lock`, draft):
- RQ3 protocol decisions: accepted Shahesta's 15 Oct proposals with amendments; added H3a as the
  primary endpoint, non-inferiority (δ = 10 points) for H3b/H3d, Holm across H3b–H3d, sample size
  from a McNemar power analysis (6 rounds = 54 paired faults per agent), replayed traffic, full
  freeze (commit, model digest, temperature, recording), seeds 101–106.
- `score.py analyze`: the whole pre-registered analysis, tested (Newcombe intervals reproduce the
  published values). Fixed: an escalation proposing `none` was scored as unneeded.
- Replay fixed: `upstream_cut` and `bad_config_collector` had no effect under replay, and a
  collector restart rewound the traffic. Replay now follows a shared `SESSION_START` and fails
  like the live stream. New tool `testbed_ops.recordings` to build the per-seed recordings.
- `run-session.sh` now takes `<agent> <rounds> <seed> <recording>`.

**Next, Shahesta** (to close PR #5; needs your Mac: Docker and the hourly recordings):
1. Review PR #5.
2. `cd testbed/ops && python -m testbed_ops.recordings scan ../recordings`, then `make` the six
   `seed101.ndjson` … `seed106.ndjson` the rule assigns (record live first if a start hour has no
   qualifying window). Post the six SHA-256 values on the PR.
3. One unscored smoke session per agent:
   `CHAOS_FAULTS=bad_release_worker,db_lock ./run-session.sh rule 1 101 seed101.ndjson`, same with
   `llm`. Check that `header.json` has the digest and `SESSION_START`, and that the report runs.

**Next, Nour:**
1. Write the six SHA-256 values into the protocol, merge PR #5, tag `rq3-protocol-locked`.
2. Meanwhile: rerun calibration and evidence-failure (`ic/eval`) on the final RQ1 file
   (`data/rq1-results/2026-10-15/`), then the RQ1 write-up with per-task numbers and the
   always-"Yes" baseline.

**Blocked on:** the recordings and smoke sessions (Shahesta's Mac).

### Remaining overall

| Track | What is left | Who |
|---|---|---|
| RQ3 live benchmark | lock the protocol (above) → pilot: seeds 101–102 for both agents → check the disagreement rate → seeds 103–106 → `score analyze` | Both; Nour scores |
| RQ1 | calibration/evidence-failure on the final file; write-up with per-task accuracy | Nour |
| Paper 1 | everything after the introduction: RQ1, RQ2, RQ3 results and discussion | Both |
| Paper 2 (Upgrade Paradox) | Experiment 2 and a second model family; note the 8B → 14B gap alone is inside the intervals | Shahesta |
| Mentor-scale test bed | days 1–3 done locally (k3d); move to AWS Stockholm Spot | Shahesta |
