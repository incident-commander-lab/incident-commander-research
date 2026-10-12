# Pulse: the RQ3 live test bed

A small real system for testing the agent on faults it has never seen. **No synthetic users:** the load is
the live Wikimedia EventStreams feed (every edit on every Wikimedia wiki, about 10–20 per second, with
day/night cycles and bursts).

```
Wikimedia EventStreams ──► collector ──► Redis stream ──► worker ──► Postgres ──► API
        (real, live)          │ recorder (hourly NDJSON, for replay)
Prometheus (+ redis/postgres exporters) ──► alert rules ──► Alertmanager ──► agent (week 2)
Grafana dashboard "Pulse test bed"
```

## Run (local)

```bash
cd testbed
USER_AGENT="pulse-testbed/0.1 (contact: <your email or repo>)" docker compose up -d --build
```

| What | Where |
|---|---|
| API | http://localhost:3300/api/top · /api/rate · /api/freshness |
| Grafana | http://localhost:3301 (dashboard: Pulse → Pulse test bed) |
| Prometheus | http://localhost:9390 (Alerts tab: 9 rules) |
| Alertmanager | http://localhost:9393 |
| Recordings | `testbed/recordings/wikipedia-YYYYMMDDHH.ndjson` |

Stop: `docker compose down` (add `-v` to also delete the database).

## Replay (fair comparisons)

Every agent under test must see identical traffic. Replay a recorded hour at its original timing:

```bash
REPLAY_FILE=/recordings/wikipedia-2026101212.ndjson REPLAY_SPEED=1 docker compose up -d collector
```

## Status

- **Week 1 (done):** collector with reconnect + resume (`Last-Event-ID`), Redis stream with consumer
  group and pending-entry recovery, idempotent Postgres writes, API, Prometheus metrics on every
  component, exporters, 9 alert rules, Grafana dashboard, recorder and replay.
- **Week 2:** fault injector with a sealed ground-truth log (bad deploy, slow DB, Redis full,
  crash loop), Alertmanager → agent webhook, agent checks over PromQL/logs/container state,
  reversible actions behind OPA, Slack escalation; then the move to the AWS host (kind).

Known gap: the API has no readers yet, so API latency/error alerts cannot fire until something
queries it (the agent and the dashboard will).
