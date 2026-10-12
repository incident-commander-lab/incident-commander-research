#!/bin/bash
# Start the live service (and the agent's model), then open the dashboard and the update document.
# Double-click in Finder, or run:  ./start-live.command
cd "$(dirname "$0")"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

if ! docker info >/dev/null 2>&1; then
  say "Starting Docker Desktop..."
  open -a Docker
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { echo "Docker did not start. Open Docker Desktop by hand and run this again."; exit 1; }
fi

if command -v ollama >/dev/null && ! curl -sf -m 2 localhost:11434/api/version >/dev/null; then
  say "Starting Ollama (the agent's AI model)..."
  (nohup ollama serve >/dev/null 2>&1 &)
fi

say "Starting the live service..."
docker compose up -d

say "Waiting for Grafana..."
for _ in $(seq 1 60); do curl -sf -m 2 "http://localhost:${GRAFANA_PORT:-3301}/api/health" >/dev/null && break; sleep 2; done

say "Waiting for the first minute of live data (so the graphs are not empty)..."
for _ in $(seq 1 45); do
  curl -s -m 3 -G "http://localhost:${PROM_PORT:-9390}/api/v1/query" \
       --data-urlencode 'query=sum(rate(pulse_events_processed_total[1m])) > 0' | grep -q '"value"' && break
  sleep 3
done

[ -z "$NO_OPEN" ] && open "http://localhost:${GRAFANA_PORT:-3301}/d/pulse/pulse-test-bed?orgId=1&from=now-15m&to=now&refresh=10s"
[ -z "$NO_OPEN" ] && open "https://ic-live-update.vercel.app"
say "Running. Live links in the document now work."
echo "Grafana     http://localhost:${GRAFANA_PORT:-3301}"
echo "Prometheus  http://localhost:${PROM_PORT:-9390}/alerts"
echo "Agent       http://localhost:${AGENT_PORT:-8380}/incidents"
echo "API         http://localhost:${API_PORT:-3300}/api/top"
echo
echo "When you are done: double-click stop-live.command (or run ./stop-live.command)"
