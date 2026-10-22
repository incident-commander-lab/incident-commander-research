#!/bin/bash
# Live-showing controls for an AWS session (after infra/gpu.sh up + deploy + ui). Runs from the laptop;
# every command is a short Session Manager call to the ops machine. Runbook: cluster/DEMO.md
#
#   cluster/demo/demo.sh load [hours]     traffic for the whole showing: German Wikipedia's daily cycle,
#                                         one day per hour, repeated for <hours> (default 3), 60 users at peak
#   cluster/demo/demo.sh inject <fault>   cpu_saturation_shop | memory_leak_assistant | gpu_memory_exhausted | assistant_bad_url
#   cluster/demo/demo.sh heal             end the active fault now
#   cluster/demo/demo.sh feed [n]         the agent's last n messages (default 3)
#   cluster/demo/demo.sh approve <id>     approve an escalated incident's proposed fix (the human in the loop)
#   cluster/demo/demo.sh act | shadow     agent autonomy: act (fixes) or shadow (only says what it would do)
#   cluster/demo/demo.sh kind rule|llm    which agent decides: the frozen rule or the plain LLM agent (RQ3 setup 2)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
K='export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; cd /root/ic'
ops() { printf '%s\n%s\n' "$K" "$1" > "$TMP/s.sh"; "$REPO/infra/gpu.sh" run "$TMP/s.sh" "${2:-120}"; }

case "${1:-}" in
load)
  H=${2:-3}; TH=$((H * 24)); M=$((H * 60 + 5))
  ops "kubectl -n load delete job --all >/dev/null 2>&1
sed -e 's/NAME/showing/' -e 's/\"USERS\"/\"60\"/g' -e 's/MINUTESm/${M}m/' -e 's|\"TRACE\"|\"/traces/wikipedia-de-hourly-2026-09-28.json\"|' -e 's/\"TRACEHOURS\"/\"$TH\"/' cluster/load/job.yaml | kubectl apply -f - >/dev/null && echo 'traffic on for $H h ($TH trace hours, 60 users at peak)'"
  ;;
inject)
  F=${2:?fault name}
  ops "kubectl -n shop delete events --all >/dev/null 2>&1; kubectl -n chaos exec deploy/chaos -- python chaos.py inject $F"
  ;;
heal)
  ops "kubectl -n chaos exec deploy/chaos -- python chaos.py heal"
  ;;
feed)         # messages start with an ISO timestamp; '----' between them (gpu.sh drops blank lines)
  ops "kubectl -n agent exec deploy/ic-agent -- cat /incidents/outbox.log 2>/dev/null | python3 -c '
import re, sys
m = [x.strip() for x in re.split(r\"\\n(?=\\d{4}-\\d\\d-\\d\\dT)\", sys.stdin.read().strip()) if x.strip()]
print(\"\\n----\\n\".join(m[-${2:-3}:]) or \"no agent messages yet\")'"
  ;;
approve)
  ID=${2:?incident id}
  ops "kubectl -n agent exec deploy/ic-agent -- python -c \"import urllib.request; print(urllib.request.urlopen(urllib.request.Request('http://localhost:8080/incidents/$ID/approve', method='POST')).read().decode())\""
  ;;
act|shadow)
  ops "kubectl -n agent set env deploy/ic-agent AUTONOMY=$1 >/dev/null && kubectl -n agent rollout status deploy/ic-agent --timeout=3m >/dev/null && echo 'agent autonomy: $1'" 300
  ;;
kind)
  K2=${2:?rule or llm}; [ "$K2" = rule ] || [ "$K2" = llm ] || { echo "kind must be rule or llm"; exit 1; }
  ops "kubectl -n agent set env deploy/ic-agent AGENT_KIND=$K2 >/dev/null && kubectl -n agent rollout status deploy/ic-agent --timeout=3m >/dev/null && echo 'agent: $K2'" 300
  ;;
*) sed -n 2,13p "$0"; exit 1 ;;
esac
