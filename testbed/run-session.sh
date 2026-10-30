#!/bin/bash
# One scored RQ3 session (docs/paper/RQ3_PROTOCOL.md). Runs in the background; the report is
# written at the end. Both agents given the same SEED get the same faults at the same times,
# on the same replayed traffic.
#
#   ./run-session.sh rule 2 101 seed101.ndjson   # frozen rule v2, 2 rounds, seed 101, replaying
#                                                # testbed/recordings/seed101.ndjson
#   ./run-session.sh llm  2 101 seed101.ndjson   # plain LLM agent, same schedule and traffic
#   ./run-session.sh status                      # what is running, faults so far (key stays sealed)
#
# A session is whole rounds only: every fault type once per round (9 slots of 25 min, about
# 3 h 45 min, plus ~25 min to settle). The recording must last at least as long as the session:
# when a replay file ends, the stream stops and the collector's alerts fire.
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

USAGE="usage: ./run-session.sh rule|llm <rounds> <seed> <recording in testbed/recordings>"
KIND=${1:?$USAGE}; ROUNDS=${2:?$USAGE}; SEED=${3:?$USAGE}; RECORDING=${4:?$USAGE}
[ "$KIND" = rule ] || [ "$KIND" = llm ] || { echo "agent must be rule or llm"; exit 1; }
[[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]] || { echo "rounds must be a whole number >= 1 (RQ3_PROTOCOL.md: whole rounds only)"; exit 1; }
[ -f "recordings/$RECORDING" ] || { echo "no recording testbed/recordings/$RECORDING (every scored session replays one)"; exit 1; }
[ -f sessions/.current ] && { echo "a session is already running:"; cat sessions/.current; exit 1; }
MODEL=${AGENT_MODEL:-qwen3:8b}
TEMPERATURE=0.3   # set in agent.py and llm_agent.py; recorded so the header says what ran
TIMEOUT=${FAULT_TIMEOUT_MIN:-15}; SLOT=${CHAOS_SLOT_MIN:-25}
if [ -n "${CHAOS_FAULTS:-}" ]; then TYPES=$(echo "$CHAOS_FAULTS" | tr ',' '\n' | grep -c .); else TYPES=9; fi
SLOTS=$((ROUNDS * TYPES))
ID="$(date +%Y%m%d-%H%M)-$KIND-seed$SEED"; OUT="sessions/$ID"

pgrep -f "ic.bench.ladder run" >/dev/null && echo "WARNING: the model ladder is running and shares the laptop's model server; the session will be slower than usual."
curl -sf -m 5 http://localhost:11434/api/tags >/dev/null || { nohup ollama serve >/dev/null 2>&1 & sleep 4; }
ollama show "$MODEL" >/dev/null 2>&1 || { echo "model $MODEL is not pulled: ollama pull $MODEL"; exit 1; }
# the tag can move to new weights; the digest identifies exactly what ran
DIGEST=$(curl -sf http://localhost:11434/api/tags | python3 -c "
import json, sys
m = sys.argv[1]
print(next((x['digest'] for x in json.load(sys.stdin)['models'] if x['name'] in (m, m + ':latest')), ''))" "$MODEL")
[ -n "$DIGEST" ] || { echo "could not read the digest of $MODEL from Ollama"; exit 1; }
mkdir -p "$OUT"

# fresh answer key and incident folder; earlier incidents are archived, never deleted
if ls incidents/*.json >/dev/null 2>&1; then
  mkdir -p "sessions/_before-$ID"; mv incidents/*.json "sessions/_before-$ID/"
  [ -f incidents/outbox.log ] && mv incidents/outbox.log "sessions/_before-$ID/"
  [ -f incidents/agent_errors.log ] && mv incidents/agent_errors.log "sessions/_before-$ID/"
fi
$DC stop chaos agent >/dev/null 2>&1 || true
$DC run --rm --no-deps --entrypoint sh chaos -c 'rm -f /sealed/ground_truth.jsonl /sealed/active.json' >/dev/null

export AGENT_KIND=$KIND AUTONOMY=act CHAOS_MODE=slots CHAOS_SEED=$SEED CHAOS_SLOTS=$SLOTS \
       CHAOS_SLOT_MIN=$SLOT FAULT_TIMEOUT_MIN=$TIMEOUT AGENT_MODEL=$MODEL \
       REPLAY_FILE=/recordings/$RECORDING REPLAY_SPEED=1
$DC build >/dev/null
# one clock for the traffic replay and the fault schedule, set after the build so build time
# cannot shift one against the other; 45 s leaves time for every container to start
export SESSION_START=$(( $(date +%s) + 45 ))
$DC up -d --force-recreate >/dev/null
python3 - "$OUT/header.json" <<PY
import json, subprocess, sys, platform, datetime
commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "."], capture_output=True, text=True).stdout.strip())
json.dump({"session": "$ID", "started_utc": datetime.datetime.utcnow().isoformat(timespec="seconds"),
           "git_commit": commit, "uncommitted_changes": dirty, "AGENT_KIND": "$KIND", "AUTONOMY": "act",
           "AGENT_MODEL": "$MODEL", "model_digest": "$DIGEST", "llm_temperature": $TEMPERATURE,
           "REPLAY_FILE": "$RECORDING", "SESSION_START": $SESSION_START, "CHAOS_MODE": "slots", "CHAOS_SEED": $SEED,
           "CHAOS_ROUNDS": $ROUNDS, "CHAOS_FAULT_TYPES": $TYPES, "CHAOS_SLOTS": $SLOTS,
           "CHAOS_SLOT_MIN": $SLOT, "FAULT_TIMEOUT_MIN": $TIMEOUT, "VERIFY_SECONDS": 120,
           "COST_ACT": 2, "COST_ESCALATE": 15, "COST_HARM": 60,
           "host": platform.node() + " " + platform.platform()}, open(sys.argv[1], "w"), indent=1)
PY
echo "$ID  started $(date '+%F %H:%M')  agent=$KIND seed=$SEED faults=$SLOTS  report: testbed/$OUT/report.txt" > sessions/.current

# wait for the last slot's fault to end for its fault to end (timeout) and the agent to settle
WAIT=$(python3 -c "print(int($SESSION_START - $(date +%s) + $SLOTS * $SLOT * 60 + ($TIMEOUT + 8) * 60))")
KEEPAWAKE=$(command -v caffeinate >/dev/null && echo "caffeinate -i" || true)   # macOS only; Linux hosts do not sleep
nohup $KEEPAWAKE "$0" _finish "$ID" "$WAIT" > "$OUT/runner.log" 2>&1 &
echo "Session $ID running: $SLOTS faults, about $(python3 -c "print(round($WAIT/3600, 1))") h."
echo "Grafana http://localhost:3301 · agent http://localhost:8380/incidents · progress: ./run-session.sh status"
