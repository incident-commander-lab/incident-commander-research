# Pulse: the RQ3 live test bed

A small real system for testing Incident Commander on faults it has never seen. **No synthetic
users:** the load is the live Wikimedia EventStreams feed (every edit on every Wikimedia wiki,
about 10–25 per second, with day/night cycles and bursts).

```
Wikimedia EventStreams ──► collector ──► Redis stream ──► worker ──► Postgres ──► API
   (real, live)              │ recorder (hourly NDJSON, for replay)
Prometheus (+ exporters) ──► 9 alert rules ──► Alertmanager ──► agent ──► OPA ──► action
                                                                  │          (allow/deny)
fault injector ──► real faults; answer key in a sealed volume     └──► Slack / outbox (escalation)
Grafana dashboard "Pulse test bed"
```

## Run it on your own machine (Shahesta or Nour)

You need **Docker Desktop** (Mac or Windows) and, for the agent's LLM, **Ollama**.

```bash
# 1. once: get the model the agent uses
ollama pull qwen3:8b

# 2. from the repo root
cd testbed
cp .env.example .env          # edit USER_AGENT (put your contact), keep the rest
docker compose up -d --build  # first time: ~5 min of image downloads
```

| What | Where |
|---|---|
| Grafana dashboard | http://localhost:3301 → Dashboards → Pulse → Pulse test bed |
| API | http://localhost:3300/api/top · /api/rate · /api/freshness |
| Prometheus (alerts) | http://localhost:9390/alerts |
| Agent | http://localhost:8380/healthz · /incidents |
| Agent decisions | `testbed/incidents/*.json` (one file per incident), messages in `incidents/outbox.log` |

If a port is already used on your machine, change it in `.env` (e.g. `API_PORT=3400`).
On Windows, run the commands in PowerShell; they are the same.

Stop: `docker compose down` (add `-v` to also delete the database and the answer key).

## Make an incident happen

```bash
docker compose exec chaos python -m testbed_ops.chaos list                     # the 5 faults
docker compose exec chaos python -m testbed_ops.chaos inject bad_release_worker
docker compose exec -d chaos python -m testbed_ops.chaos inject db_lock         # -d: it holds the lock
docker compose exec chaos python -m testbed_ops.chaos heal                     # end it by hand
```

| Fault | What really happens | Correct response | Seen? |
|---|---|---|---|
| `bad_release_worker` | a worker release with a bug (writes a column that doesn't exist) | roll the worker back | seen (shakedown) |
| `crash_loop_worker` | a worker release with an invalid config: it exits, restarts, exits | roll the worker back | unseen |
| `bad_config_collector` | a collector release pointing at a wrong stream URL | roll the collector back | unseen |
| `db_lock` | an "ad-hoc maintenance query" takes an exclusive table lock and never commits | end the blocking session | seen (shakedown) |
| `redis_oom` | Redis `maxmemory` set far too low: new events are rejected | restore `maxmemory` | unseen |
| `worker_hung` | the worker process is frozen (SIGSTOP): no errors, no deploy, Docker still says "running" | restart the worker | unseen (added 15 Oct) |
| `collector_hung` | the same for the collector: the stream and its metrics go silent | restart the collector | unseen (added 15 Oct) |
| `api_down` | the API container is stopped | restart the API | unseen (added 15 Oct) |
| `upstream_cut` | the internet path to Wikimedia is cut (the `egress` relay stops); everything we own is healthy | **do nothing** (wait or escalate): no fix on the allowlist helps | unseen (added 15 Oct) |

`upstream_cut` is the test of acting when the agent should not: the symptoms look like a
collector problem, but restarting or rolling back the collector cannot help. The collector
reaches Wikimedia through the `egress` relay (TLS passes through untouched); the agent has no
check on the relay, the same way it has no check on a real ISP.

Random, unannounced faults: set `CHAOS_MODE=schedule` in `.env` (mean gap `CHAOS_MEAN_GAP_MIN`).
Faults the agent does not fix are healed by the injector after `FAULT_TIMEOUT_MIN`.
Real-world incidents also happen on their own (stream disconnects): the right response there
is to **not** change anything.

**The answer key** is in the `sealed` Docker volume, which only the injector mounts. To read it
for scoring (after a session, never during):
`docker compose exec chaos cat /sealed/ground_truth.jsonl`

## Scored sessions (RQ3)

```bash
./run-session.sh rule 1 101 seed101.ndjson   # frozen rule v2, 1 round (9 faults, ~4.2 h), seed 101
./run-session.sh llm  1 101 seed101.ndjson   # the plain LLM agent on the SAME schedule and traffic
./run-session.sh status                      # progress; the answer key stays sealed until the end
```

Sessions run **whole rounds** (every fault type once per round) and **replay** a recording from
`recordings/` that lasts at least as long as the session. The header records the repo commit,
the model's Ollama digest, the LLM temperature and the recording. Analyse all sessions of both
agents at once (paired by seed; the pre-registered H3a-H3d tests):

```bash
cd ops && python -m testbed_ops.score analyze ../sessions/*-seed*
```

A session wipes the answer key, archives old incidents, starts the chosen agent at
`AUTONOMY=act` with a fixed schedule (`CHAOS_MODE=slots`: one fault per 25-minute slot, every
fault type once per round in a seeded order, start times jittered by up to 3 minutes, faults
time out after 15 minutes), and runs in the background. At the end it copies the answer key
and incidents to `sessions/<id>/`, writes `report.txt` with the scorer, puts the test bed back
in manual mode and shows a Mac notification. Compare two agents on the same seed:

```bash
cd ops && python -m testbed_ops.score compare ../sessions/<A>/ground_truth.jsonl ../sessions/<A> \
                                              ../sessions/<B>/ground_truth.jsonl ../sessions/<B>
```

**The plain LLM agent** (`AGENT_KIND=llm`, `ops/testbed_ops/llm_agent.py`) gets the same
alerts, the same free evidence, the same checks and fixes, and the same safety net (OPA, the
0.78 act threshold applied to its own stated confidence, verification and auto-revert). The
only difference: the LLM chooses every check, when to stop, the cause, the fix and its
confidence. No Bayesian update, no value-of-information stop.

## Autonomy levels (`AUTONOMY` in `.env`)

| Level | What the agent does |
|---|---|
| `shadow` | investigates and records what it *would* do; changes nothing |
| `suggest` | posts the diagnosis, evidence and proposed fix; a human approves with one command |
| `act` | acts on its own only if OPA allows it (allowlisted, reversible, confident enough), verifies recovery from fresh telemetry after `VERIFY_SECONDS`, and reverts + escalates if recovery does not hold |

In every mode, an escalation includes the proposed fix and a one-line approve command:
`curl -X POST http://localhost:8380/incidents/<id>/approve`

## How the agent decides (frozen for RQ3)

1. Free evidence: every container's state.
2. One LLM call: competing hypotheses, each with predicted check outcomes and a fix from the allowlist.
3. **The benchmark's rule v2, unchanged** (`ic/bench/decide.py`), picks checks by value of
   information and stops when no check is worth its cost.
4. Decision-aware checks: if the *fix* is still uncertain, it keeps checking only while a check
   is expected to reduce uncertainty about which fix works.
5. P(fix works) = Σ P(hypothesis) × [the fix resolves it]; a rollback of a just-changed service
   resolves every cause on that service. Act if P > 1 − (escalate − act)/harm = **0.78**.

**Shakedown before freezing (12 Oct):** run on `bad_release_worker` (4 tries) and `db_lock` (1 try).
That exposed and fixed: a stale 2-minute throughput window (now 45 s); stopping when the
*diagnosis* was certain but the *fix* was not (now step 4); and choosing the top hypothesis's
fix instead of the fix most likely to work (now step 5). The final `bad_release_worker` run
diagnosed, rolled back and verified recovery on its own; `db_lock` was localized to Postgres
with the right fix proposed but escalated (p = 0.32). **The agent logic is now frozen:** any
change after this point must be reported, and these two faults count as seen.

## Replay (fair comparisons)

```bash
REPLAY_FILE=/recordings/wikipedia-2026101212.ndjson REPLAY_SPEED=1 docker compose up -d collector
```

## Known gaps

- The API has no readers yet, so API latency/error alerts rarely fire.
- Runs locally with Docker Compose; the move to the AWS host comes with the RQ3 sessions.
- The agent's Docker-socket access is broad (it can see all containers). Fine for a test bed;
  OPA limits what it may *do*.
