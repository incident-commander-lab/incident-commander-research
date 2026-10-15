#!/bin/bash
# One scored RQ3 session (docs/paper/RQ3_PROTOCOL.md). Runs in the background; the report is
# written at the end. Both agents given the same SEED get the same faults at the same times.
#
#   ./run-session.sh rule 5 101      # our frozen rule v2, 5 hours, seed 101
#   ./run-session.sh llm  5 101      # plain LLM agent, same schedule
#   ./run-session.sh status          # what is running, faults so far (answer key stays sealed)
#
# Needs: Docker Desktop, Ollama with AGENT_MODEL (default qwen3:8b). Keep the laptop plugged in.
set -euo pipefail
cd "$(dirname "$0")"
DC="docker compose"

finish() {   # finish <ID>: collect the answer key and incidents, score, back to manual mode
  set +e
  local ID=$1 OUT="sessions/$1"
  $DC exec -T chaos python -m testbed_ops.chaos heal >/dev/null 2>&1
  $DC exec -T chaos cat /sealed/ground_truth.jsonl > "$OUT/ground_truth.jsonl"
  CHAOS_MODE=serve AUTONOMY=shadow AGENT_KIND=rule $DC up -d chaos agent >/dev/null 2>&1   # back to manual mode
  cp incidents/*.json "$OUT/" 2>/dev/null; cp incidents/outbox.log incidents/agent_errors.log "$OUT/" 2>/dev/null
  (cd ops && python3 -m testbed_ops.score "../$OUT/ground_truth.jsonl" "../$OUT") > "$OUT/report.txt" 2>&1
  rm -f sessions/.current
  osascript -e "display notification \"Session $ID finished. Report: testbed/$OUT/report.txt\" with title \"Pulse test bed\" sound name \"Glass\"" 2>/dev/null
}

if [ "${1:-}" = "_finish" ]; then   # internal: _finish <ID> <seconds>, run under nohup by the start below
  sleep "$3"; finish "$2"; exit 0
fi

if [ "${1:-}" = "status" ]; then
  cat sessions/.current 2>/dev/null || { echo "no session running"; exit 0; }
  echo "incidents so far: $(ls incidents/*.json 2>/dev/null | wc -l | tr -d ' ')"
  echo "faults so far:    $($DC exec -T chaos sh -c 'grep -c "\"inject\"" /sealed/ground_truth.jsonl' 2>/dev/null || echo ?)"
  exit 0
fi

KIND=${1:?usage: ./run-session.sh rule|llm <hours> <seed>}; HOURS=${2:?hours}; SEED=${3:?seed}
[ "$KIND" = rule ] || [ "$KIND" = llm ] || { echo "agent must be rule or llm"; exit 1; }
[ -f sessions/.current ] && { echo "a session is already running:"; cat sessions/.current; exit 1; }
MODEL=${AGENT_MODEL:-qwen3:8b}
TIMEOUT=${FAULT_TIMEOUT_MIN:-15}; SLOT=${CHAOS_SLOT_MIN:-25}
SLOTS=$(python3 -c "print(max(1, int($HOURS * 60 // $SLOT)))")
ID="$(date +%Y%m%d-%H%M)-$KIND-seed$SEED"; OUT="sessions/$ID"
mkdir -p "$OUT"

pgrep -f "ic.bench.ladder run" >/dev/null && echo "WARNING: the model ladder is running and shares the laptop's model server; the session will be slower than usual."
curl -sf -m 5 http://localhost:11434/api/tags >/dev/null || { nohup ollama serve >/dev/null 2>&1 & sleep 4; }
ollama show "$MODEL" >/dev/null 2>&1 || { echo "model $MODEL is not pulled: ollama pull $MODEL"; exit 1; }

# fresh answer key and incident folder; earlier incidents are archived, never deleted
if ls incidents/*.json >/dev/null 2>&1; then
  mkdir -p "sessions/_before-$ID"; mv incidents/*.json "sessions/_before-$ID/"
  [ -f incidents/outbox.log ] && mv incidents/outbox.log "sessions/_before-$ID/"
  [ -f incidents/agent_errors.log ] && mv incidents/agent_errors.log "sessions/_before-$ID/"
fi
$DC stop chaos agent >/dev/null 2>&1 || true
$DC run --rm --no-deps --entrypoint sh chaos -c 'rm -f /sealed/ground_truth.jsonl /sealed/active.json' >/dev/null

export AGENT_KIND=$KIND AUTONOMY=act CHAOS_MODE=slots CHAOS_SEED=$SEED CHAOS_SLOTS=$SLOTS \
       CHAOS_SLOT_MIN=$SLOT FAULT_TIMEOUT_MIN=$TIMEOUT AGENT_MODEL=$MODEL
$DC up -d --build >/dev/null
python3 - "$OUT/header.json" <<PY
import json, subprocess, sys, platform, datetime
commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "."], capture_output=True, text=True).stdout.strip())
json.dump({"session": "$ID", "started_utc": datetime.datetime.utcnow().isoformat(timespec="seconds"),
           "git_commit": commit, "uncommitted_changes": dirty, "AGENT_KIND": "$KIND", "AUTONOMY": "act",
           "AGENT_MODEL": "$MODEL", "CHAOS_MODE": "slots", "CHAOS_SEED": $SEED, "CHAOS_SLOTS": $SLOTS,
           "CHAOS_SLOT_MIN": $SLOT, "FAULT_TIMEOUT_MIN": $TIMEOUT, "VERIFY_SECONDS": 120,
           "COST_ACT": 2, "COST_ESCALATE": 15, "COST_HARM": 60, "hours": $HOURS,
           "host": platform.node() + " " + platform.platform()}, open(sys.argv[1], "w"), indent=1)
PY
echo "$ID  started $(date '+%F %H:%M')  agent=$KIND seed=$SEED faults=$SLOTS  report: testbed/$OUT/report.txt" > sessions/.current

# the last slot starts before HOURS; wait for its fault to end (timeout) and the agent to settle
WAIT=$(python3 -c "print(int($SLOTS * $SLOT * 60 + ($TIMEOUT + 8) * 60))")
nohup caffeinate -i "$0" _finish "$ID" "$WAIT" > "$OUT/runner.log" 2>&1 &
echo "Session $ID running: $SLOTS faults, about $(python3 -c "print(round($WAIT/3600, 1))") h."
echo "Grafana http://localhost:3301 · agent http://localhost:8380/incidents · progress: ./run-session.sh status"
