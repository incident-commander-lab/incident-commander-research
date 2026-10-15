#!/bin/bash
# Incident Commander — RQ1 benchmark runbook (one command per step).
#
#   ~/ic-bench.sh up        first time only (~25 min): AWS machine + Kubernetes cluster + code
#   ~/ic-bench.sh night 1   qwen3:8b — ours, random, run-everything   (~13 h)
#   ~/ic-bench.sh night 2   qwen3:8b — plain LLM, LLM-chooser          (~13 h)
#   ~/ic-bench.sh night 3   qwen3:14b — plain LLM, ours                (~14 h, optional)
#   ~/ic-bench.sh night 4   rule v2 (8B, then 14B) + 3 plain-LLM retries
#   ~/ic-bench.sh night 5   qwen3:8b — re-run every run that touched a broken check (catalog v2 fix)  (~10 h)
#   ~/ic-bench.sh night 6   qwen3:14b — same re-runs + the 14B LLM-chooser                          (~10 h)
#   ~/ic-bench.sh status    progress, any time (also safe while a night runs)
#   ~/ic-bench.sh watch     status, refreshed every 5 min (Ctrl-C to stop watching; the run continues)
#   ~/ic-bench.sh redo <text>  forget results whose problem name contains <text>, so the next night re-runs them
#   ~/ic-bench.sh fetch     copy results to the laptop
#   ~/ic-bench.sh credits   AWS credits used / left (nights refuse to start below $15 left)
#   ~/ic-bench.sh hostrun <s> <script>  run a bash script on the machine (starts it if stopped)
#   ~/ic-bench.sh table     results table (after fetch)
#   ~/ic-bench.sh pause     stop tunnel/model/keep-awake and stop the AWS machine (disk kept)
#   ~/ic-bench.sh down      fetch, then delete the AWS stack ($0/day)
#
# Nights are resumable: a run already finished is skipped, so re-running a night
# after an interruption just continues. Keep the laptop plugged in, online, lid open.
set -euo pipefail

REPO="${IC_REPO:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}"   # repo root (override with IC_REPO)
BRANCH="research/voi-benchmark"
PROFILE=ic-bench; REGION=us-east-1; STACK=ic-bench
EMAIL="${IC_BUDGET_EMAIL:?set IC_BUDGET_EMAIL to the address that should get AWS budget alerts}"
KEY="$HOME/.ssh/ic-bench-tunnel"
LOCAL_RESULTS="$REPO/results/aiopslab_full_rq1"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }
instance_id() { aws_ cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue|[0]" --output text 2>/dev/null || true; }
state() { aws_ ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo none; }

# Run a bash script on the host as user ubuntu: onhost <timeout-seconds> <script-file>
onhost() {
  local T=$1 F=$2 ID B64 PARAMS
  ID=$(instance_id); B64=$(base64 < "$F" | tr -d '\n')
  PARAMS=$(python3 -c 'import json,sys; print(json.dumps({"command":["bash -c \"echo %s | base64 -d | sudo -iu ubuntu bash\"" % sys.argv[1]]}))' "$B64")
  # hard timeout on the aws process itself (a session to a still-booting machine can hang forever)
  perl -e 'alarm shift; exec @ARGV' "$T" aws --profile "$PROFILE" --region "$REGION" ssm start-session \
    --target "$ID" --document-name AWS-StartNonInteractiveCommand \
    --parameters "$PARAMS" < <(sleep "$T") 2>&1 \
    | grep -v -e "^Starting session" -e "^Exiting session" -e '^\s*$' || true
}
host() { printf '%s\n' "$2" > "$TMP/s.sh"; onhost "$1" "$TMP/s.sh"; }

wait_ssm() {
  printf 'waiting for the machine'
  for _ in $(seq 1 60); do host 30 'echo SSM_READY' | grep -q SSM_READY && { echo " ready"; return; }; printf .; sleep 10; done
  echo; echo "machine did not answer — try again in a few minutes"; exit 1
}

# --- credit guard -------------------------------------------------------------
# Credits left: read from the Free Tier API (exact, needs the ic-gpu-stockholm policy); fallback:
# ~/.ic-credits CREDITS_TOTAL=<dollars granted> minus all credits used so far (Cost Explorer).
CREDIT_FLOOR=${CREDIT_FLOOR:-15}   # refuse to start paid work below this many dollars left
credit_report() {   # prints "used_credits usage_month remaining|unknown"; one Cost Explorer call ($0.01)
  local J EXACT; EXACT=$(aws_ freetier get-account-plan-state --query 'accountPlanRemainingCredits.amount' --output text 2>/dev/null || true)
  J=$(aws_ ce get-cost-and-usage --time-period "Start=2025-10-01,End=$(date -u -v+1d +%F 2>/dev/null || date -u -d tomorrow +%F)" \
    --granularity MONTHLY --metrics UnblendedCost --group-by Type=DIMENSION,Key=RECORD_TYPE --output json 2>/dev/null) || { echo "? ? unknown"; return; }
  CREDITS_TOTAL=""; [ -f "$HOME/.ic-credits" ] && . "$HOME/.ic-credits"
  python3 -c '
import json,sys
d=json.loads(sys.argv[1]); used=0.0; month=0.0
for i,m in enumerate(d["ResultsByTime"]):
    for g in m["Groups"]:
        a=float(g["Metrics"]["UnblendedCost"]["Amount"])
        if g["Keys"][0]=="Credit": used-=a
        if g["Keys"][0]=="Usage" and i==len(d["ResultsByTime"])-1: month+=a
t=sys.argv[2]
e=sys.argv[3]
left=f"{float(e):.2f}" if e.replace(".","",1).isdigit() else (f"{float(t)-used:.2f}" if t else "unknown")
print(f"{used:.2f} {month:.2f} {left}")' "$J" "${CREDITS_TOTAL:-}" "${EXACT:-}"
}
credit_guard() {    # stop before paid work if the credits are nearly gone (override: FORCE=1)
  local R; read -r USED MONTH LEFT <<< "$(credit_report)"
  echo "AWS credits: used \$$USED so far, this month's usage \$$MONTH, left: \$$LEFT"
  [ "$LEFT" = "unknown" ] && { echo "  (set CREDITS_TOTAL in ~/.ic-credits to track what is left)"; return; }
  if python3 -c "import sys; sys.exit(0 if float('$LEFT') < float('$CREDIT_FLOOR') else 1)"; then
    [ "${FORCE:-0}" = 1 ] && { echo "  below \$$CREDIT_FLOOR, continuing because FORCE=1"; return; }
    echo "  STOP: less than \$$CREDIT_FLOOR of credits left. Add credits first, or re-run with FORCE=1."; exit 1
  fi
}

ensure_running() {
  local ID S; ID=$(instance_id)
  [ -z "$ID" ] || [ "$ID" = "None" ] && { echo "No AWS stack. Run: ~/ic-bench.sh up"; exit 1; }
  S=$(state "$ID")
  if [ "$S" != "running" ]; then
    say "Starting the AWS machine ($S → running)"
    [ "$S" = "stopping" ] && aws_ ec2 wait instance-stopped --instance-ids "$ID"
    aws_ ec2 start-instances --instance-ids "$ID" >/dev/null
    aws_ ec2 wait instance-running --instance-ids "$ID"
  fi
  wait_ssm
}

sync_code() {
  say "Copying the latest benchmark code to the machine"
  git -C "$REPO" archive "$BRANCH" ic/bench ic/llm.py ic/live/beliefs.py | gzip -9 | base64 | tr -d '\n' > "$TMP/code.b64"
  split -b 2500 "$TMP/code.b64" "$TMP/chunk_"
  local n=0
  for c in "$TMP"/chunk_*; do
    n=$((n+1)); { [ $n = 1 ] && echo 'rm -f ~/code.b64'; echo "printf %s '$(cat "$c")' >> ~/code.b64"; } > "$TMP/s.sh"
    onhost 60 "$TMP/s.sh" >/dev/null
  done
  local want; want=$(shasum -a 256 < "$TMP/code.b64" | cut -c1-16)
  local got; got=$(host 60 'sha256sum < ~/code.b64 | cut -c1-16; cd ~/incident-commander && base64 -d ~/code.b64 | tar xzf -' | head -1)
  [ "$want" = "$got" ] && echo "code synced ($want)" || { echo "code copy failed ($want vs $got) — run the command again"; exit 1; }
  install_runner
}

install_runner() {
  cat > "$TMP/runner.sh" <<'RUNNER'
cat > ~/run_chunk.sh <<'EOF'
#!/bin/bash
# run_chunk.sh <ollama-model> <policy...> — resumable; skips runs already recorded without an LLM error
MODEL=$1; shift; POLICIES="$*"
cd ~/AIOpsLab
export PYTHONPATH=~/incident-commander IC_USE_LLM=1 IC_STRICT_PROVIDER=1 IC_PROVIDER=openrouter_models \
       IC_OPENROUTER_MODELS=ollama:$MODEL IC_OLLAMA_BASE_URL=http://127.0.0.1:11434/v1 IC_LLM_TIMEOUT=400
OUT=~/results/full_rq1.jsonl; LOGS=~/results/full_logs; PROG=~/results/full_progress.txt; mkdir -p "$LOGS"
PROBLEMS=$(~/.local/bin/poetry run python -c "from aiopslab.orchestrator.problems.registry import ProblemRegistry as R; r=R(); print(' '.join(i for i in r.get_problem_ids() if 'mitigation' not in i and r.get_problem_deployment(i) == 'k8s'))" 2>/dev/null | tail -1)
# Docker-deployed problems (flower) are out of scope: the probe catalog is Kubernetes-only, and they hang here.
echo "$PROBLEMS" | wc -w | tr -d ' ' > ~/results/problem_count.txt
done_already() { [ -f "$OUT" ] && python3 - "$OUT" "$1" "$2" "$MODEL" <<'PY'
import json, sys
out, prob, pol, model = sys.argv[1:]
for line in open(out):
    r = json.loads(line)
    if r.get("problem") == prob and r.get("policy") == pol and r.get("model") == model and not r.get("llm_error"):
        sys.exit(0)
sys.exit(1)
PY
}
# start clean: a pause or crash may have left a half-deployed app or monitoring behind
pkill -f "kubectl port-forward" 2>/dev/null
for ns in test-hotel-reservation test-social-network astronomy-shop; do kubectl delete ns $ns --ignore-not-found --wait=true --timeout=300s >/dev/null 2>&1; done
helm uninstall prometheus -n observe >/dev/null 2>&1
kubectl delete ns observe openebs --ignore-not-found --wait=true --timeout=300s >/dev/null 2>&1
echo "$(date -u '+%F %T') CHUNK_START $MODEL $POLICIES (cluster cleaned)" >> "$PROG"
for pol in $POLICIES; do for p in $PROBLEMS; do
  done_already "$p" "$pol" && continue
  # the model must be reachable; ride out slow answers and short Wi-Fi drops (retry up to 30 min)
  tries=0
  until curl -sf -m 20 http://127.0.0.1:11434/api/version >/dev/null; do
    tries=$((tries+1))
    [ $tries -eq 1 ] && echo "$(date -u '+%F %T') WAITING: model not reachable, retrying" >> "$PROG"
    if [ $tries -ge 60 ]; then
      echo "$(date -u '+%F %T') STOPPED: tunnel to the laptop is down (30 min)" >> "$PROG"; exit 1; fi
    sleep 30
  done
  echo "$(date -u '+%F %T') START $MODEL $pol $p" >> "$PROG"
  timeout ${IC_RUN_TIMEOUT:-2700} ~/.local/bin/poetry run python -m ic.bench.run_aiopslab --policy "$pol" --problems "$p" \
      --max-steps 30 --out "$OUT" > "$LOGS/${MODEL//:/_}__${pol}__${p}.log" 2>&1
  echo "$(date -u '+%F %T') END   $MODEL $pol $p exit=$?" >> "$PROG"
  pkill -f "kubectl port-forward" 2>/dev/null
  for ns in test-hotel-reservation test-social-network astronomy-shop; do
    kubectl get ns $ns >/dev/null 2>&1 && kubectl delete ns $ns --wait=true --timeout=300s >/dev/null 2>&1; done
done; done
echo "$(date -u '+%F %T') CHUNK_DONE $MODEL $POLICIES" >> "$PROG"
EOF
chmod +x ~/run_chunk.sh; mkdir -p ~/results; echo runner-installed
RUNNER
  onhost 60 "$TMP/runner.sh" | grep -q runner-installed && echo "runner installed" || { echo "runner install failed"; exit 1; }
}

archive_catalog_v1() {   # once: set aside every run that touched a broken check, so nights 5-6 re-run it
  host 60 "python3 - <<'PY'
import json, os
p = os.path.expanduser('~/results/full_rq1.jsonl')
a = os.path.expanduser('~/results/full_rq1_catalog_v1_archive.jsonl')
if os.path.exists(a):
    print('catalog v1 runs already archived'); raise SystemExit
ALIAS = {'flagd', 'jaeger-out'}
def touched(r):
    return any(isinstance(s, dict) and s.get('probe', ':').split(':', 1)[1] in ALIAS for s in r.get('trace') or [])
rows = [json.loads(l) for l in open(p)]
old = [r for r in rows if r.get('catalog_version') is None and touched(r)]
keep = [r for r in rows if not (r.get('catalog_version') is None and touched(r))]
open(a, 'w').writelines(json.dumps(r) + '\\n' for r in old)
open(p, 'w').writelines(json.dumps(r) + '\\n' for r in keep)
print(f'archived {len(old)} catalog-v1 runs to {a}; they will be re-run')
PY"
}

start_laptop_side() {   # start_laptop_side <model>
  say "Laptop: model server, tunnel, keep-awake"
  pgrep -x ollama >/dev/null || (OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 nohup ollama serve > "$HOME/.ollama/logs/serve-bench.log" 2>&1 &)
  for _ in $(seq 1 20); do curl -sf 127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
  ollama list 2>/dev/null | grep "^$1" >/dev/null || { echo "model $1 is not downloaded: ollama pull $1"; exit 1; }
  curl -s 127.0.0.1:11434/api/generate -d "{\"model\":\"$1\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":\"24h\"}" >/dev/null
  pkill -f "ssh -i.*ic-bench-tunnel" 2>/dev/null || true; sleep 1
  git -C "$REPO" show "$BRANCH:infra/tunnel.sh" > "$HOME/.ic-bench-tunnel.sh"; chmod +x "$HOME/.ic-bench-tunnel.sh"
  # keep the tunnel alive all night: if it drops (Wi-Fi blip), reconnect after 10 s
  ( nohup bash -c "while true; do '$HOME/.ic-bench-tunnel.sh' '$(instance_id)'; sleep 10; done" > "$HOME/.ic-bench-tunnel.log" 2>&1 & )
  pkill -x caffeinate 2>/dev/null || true; ( nohup caffeinate -dims -t 57600 >/dev/null 2>&1 & )
  sleep 15
  host 40 'curl -sf -m 10 http://127.0.0.1:11434/api/version >/dev/null && echo TUNNEL_OK || echo TUNNEL_FAIL' | grep -q TUNNEL_OK \
    && echo "tunnel OK: the AWS machine can reach your laptop's model" \
    || { echo "tunnel FAILED — see ~/.ic-bench-tunnel.log"; exit 1; }
}

cmd="${1:-help}"
case "$cmd" in
up)
  if [ -n "$(instance_id)" ] && [ "$(instance_id)" != "None" ]; then echo "Stack already exists ($(instance_id)). Use: night 1"; exit 0; fi
  say "Creating the AWS machine (stack $STACK)"
  git -C "$REPO" show "$BRANCH:infra/bench-host.yaml" > "$TMP/bench-host.yaml"
  aws_ cloudformation create-stack --stack-name "$STACK" --template-body "file://$TMP/bench-host.yaml" \
    --capabilities CAPABILITY_IAM --parameters "ParameterKey=BudgetEmail,ParameterValue=$EMAIL" >/dev/null
  aws_ cloudformation wait stack-create-complete --stack-name "$STACK"
  echo "machine $(instance_id) created"
  wait_ssm
  printf 'installing tools on it'; for _ in $(seq 1 60); do host 30 'test -f ~/BOOTSTRAP_DONE && echo BOOT_OK' | grep -q BOOT_OK && break; printf .; sleep 20; done; echo
  say "Registering the tunnel key and building the Kubernetes cluster (~15 min)"
  test -f "$KEY" || ssh-keygen -q -t ed25519 -N "" -C ic-bench-tunnel -f "$KEY"
  host 60 "mkdir -p ~/.ssh; chmod 700 ~/.ssh; echo 'restrict,port-forwarding,permitlisten=\"localhost:11434\",command=\"echo tunnel-only\" $(cat "$KEY.pub")' > ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys
cd ~/AIOpsLab && sed -e 's/^k8s_host: .*/k8s_host: kind/' -e 's/^k8s_user: .*/k8s_user: ubuntu/' aiopslab/config.yml.example > aiopslab/config.yml
cat > ~/setup_cluster.sh <<'EOF'
cd ~/AIOpsLab && kind create cluster --config kind/kind-config-x86.yaml --wait 5m && ~/.local/bin/poetry env use python3.12 && ~/.local/bin/poetry install --no-interaction && ~/.local/bin/poetry run pip -q install httpx anthropic python-dotenv && echo SETUP_FINISHED
EOF
nohup bash ~/setup_cluster.sh > ~/setup_cluster.log 2>&1 &
echo started" >/dev/null
  printf 'building'; for _ in $(seq 1 90); do host 30 'grep -q SETUP_FINISHED ~/setup_cluster.log && echo DONE' | grep -q DONE && break; printf .; sleep 20; done; echo
  host 30 'kubectl get nodes --no-headers'
  sync_code
  say "Ready. Next, when you can leave the laptop on for the night:  ~/ic-bench.sh night 1"
  ;;
night)
  case "${2:-}" in
    1) MODEL=qwen3:8b;  POLICIES="voi random exhaustive";;
    2) MODEL=qwen3:8b;  POLICIES="react llm_choose";;
    3) MODEL=qwen3:14b; POLICIES="react voi";;
    4) MODEL=qwen3:14b; POLICIES="voi2 (8B, then 14B) + 3 react retries";;  # rule v2 rerun
    5) MODEL=qwen3:8b;  POLICIES="voi voi2 random exhaustive llm_choose";;   # catalog v2 re-runs
    6) MODEL=qwen3:14b; POLICIES="voi voi2 llm_choose";;                     # catalog v2 re-runs + 14B LLM-chooser
    *) echo "usage: ~/ic-bench.sh night 1|2|3|4|5|6"; exit 1;;
  esac
  credit_guard; ensure_running; sync_code; start_laptop_side "$MODEL"
  case "$2" in 5|6) archive_catalog_v1;; esac
  say "Starting night $2: $MODEL — $POLICIES"
  if [ "$2" = 6 ]; then
    host 30 "nohup bash -c 'IC_RUN_TIMEOUT=5400 ~/run_chunk.sh qwen3:14b voi voi2 llm_choose' > ~/results/chunk_driver.log 2>&1 & sleep 3; tail -1 ~/results/full_progress.txt"
  elif [ "$2" = 4 ]; then
    # both models in one go; 14B react retries get 90 min (Low Power Mode timeouts at 45)
    host 30 "nohup bash -c '~/run_chunk.sh qwen3:8b voi2; IC_RUN_TIMEOUT=5400 ~/run_chunk.sh qwen3:14b voi2 react; echo \"\$(date -u \"+%F %T\") NIGHT_DONE 4\" >> ~/results/full_progress.txt' > ~/results/chunk_driver.log 2>&1 & sleep 3; tail -1 ~/results/full_progress.txt"
  else
    host 30 "nohup ~/run_chunk.sh $MODEL $POLICIES > ~/results/chunk_driver.log 2>&1 & sleep 3; tail -1 ~/results/full_progress.txt"
  fi
  pkill -f "ic-bench-watch" 2>/dev/null || true
  ( nohup bash -c 'exec -a ic-bench-watch bash "$0" _watch "$1"' "$0" "$2" > "$HOME/.ic-bench-watch.log" 2>&1 & )
  say "Running. You will get a Mac notification (with a sound) when night $2 finishes or if it stops early."
  echo "Leave the laptop plugged in, online, lid open. Check any time:  ~/ic-bench.sh status"
  ;;
_watch)
  # background: every 10 min, read the host's progress file; notify once on finish or stop
  while true; do
    sleep 600
    L=$(host 60 'tail -1 ~/results/full_progress.txt 2>/dev/null' | tail -1)
    case "$L" in
      *CHUNK_DONE*|*NIGHT_DONE*)
        case "${2:-}:$L" in 4:*NIGHT_DONE*|[12356]:*) ;; *) continue;; esac  # night 4 = two chunks
        osascript -e "display notification \"Night ${2:-} finished. Run: ~/ic-bench.sh pause\" with title \"Incident Commander benchmark\" sound name \"Glass\""; exit 0;;
      *STOPPED*)    osascript -e "display notification \"Night ${2:-} stopped early (tunnel down). Run the same night command again.\" with title \"Incident Commander benchmark\" sound name \"Basso\""; exit 0;;
    esac
  done
  ;;
redo)
  [ -z "${2:-}" ] && { echo "usage: ~/ic-bench.sh redo <part of a problem name>"; exit 1; }
  ensure_running
  host 60 "python3 - <<'PY'
import json, os
p = os.path.expanduser('~/results/full_rq1.jsonl')
rows = [json.loads(l) for l in open(p)]
keep = [r for r in rows if '$2' not in r.get('problem', '')]
os.rename(p, p + '.before-redo')
open(p, 'w').writelines(json.dumps(r) + '\\n' for r in keep)
print(f'removed {len(rows) - len(keep)} results matching $2; the next night command re-runs them')
PY"
  ;;
hostrun)
  # hostrun <timeout-seconds> <local script>: run a bash script on the machine as ubuntu (starts it if stopped)
  [ -f "${3:-}" ] || { echo "usage: ~/ic-bench.sh hostrun <seconds> <script>"; exit 1; }
  credit_guard; ensure_running; onhost "$2" "$3"
  ;;
credits)
  # AWS credits: used so far, this month's usage, and what is left (one Cost Explorer call, $0.01)
  credit_guard
  ;;
status)
  ID=$(instance_id); echo "AWS machine: ${ID:-none} ($( [ -n "$ID" ] && state "$ID" || echo -))"
  pgrep -f "ssh -i.*ic-bench-tunnel" >/dev/null && echo "tunnel: up" || echo "tunnel: down"
  curl -sf -m 3 127.0.0.1:11434/api/version >/dev/null && echo "model server: up" || echo "model server: down"
  pgrep -x caffeinate >/dev/null && echo "keep-awake: on" || echo "keep-awake: off"
  [ -n "$ID" ] && [ "$(state "$ID")" = "running" ] && host 60 '
echo
LAST=$(tail -1 ~/results/full_progress.txt 2>/dev/null)
case "$LAST" in
  *NIGHT_DONE*|*CHUNK_DONE*) echo ">>> NIGHT FINISHED. Next: ~/ic-bench.sh pause  (then the next night when you can)";;
  *WAITING*)    echo ">>> WAITING for the laptop model (retrying for up to 30 min): $LAST";;
  *STOPPED*)    echo ">>> STOPPED EARLY: $LAST. Run the same night command again; it continues where it left off.";;
  *PAUSED*)     echo ">>> PAUSED. Run the same night command again to continue.";;
  "")           echo ">>> nothing started yet";;
  *)            echo ">>> RUNNING. Last step: $LAST";;
esac
python3 - <<"PY"
import json, collections, os, re, datetime
NIGHTS = {("qwen3:8b", frozenset({"voi", "voi2", "random", "exhaustive", "llm_choose"})): 5,
          ("qwen3:14b", frozenset({"voi", "voi2", "llm_choose"})): 6,
          ("qwen3:8b", frozenset({"voi", "random", "exhaustive"})): 1,
          ("qwen3:8b", frozenset({"react", "llm_choose"})): 2,
          ("qwen3:14b", frozenset({"react", "voi"})): 3,
          ("qwen3:8b", frozenset({"voi2"})): 4,
          ("qwen3:14b", frozenset({"voi2", "react"})): 4}
PER_POLICY = int(open(os.path.expanduser("~/results/problem_count.txt")).read().strip() or 73) if os.path.exists(os.path.expanduser("~/results/problem_count.txt")) else 73
prog = os.path.expanduser("~/results/full_progress.txt")
out = os.path.expanduser("~/results/full_rq1.jsonl")
lines = open(prog).read().splitlines() if os.path.exists(prog) else []
starts = [l for l in lines if " START " in l]
if not starts:
    raise SystemExit
model, pol = starts[-1].split()[3:5]
night = next(((n, ps) for (m, ps), n in NIGHTS.items() if m == model and pol in ps), (None, frozenset({pol})))
n, policies = night
rows = [json.loads(l) for l in open(out)] if os.path.exists(out) else []
done = sum(1 for r in rows if r.get("model") == model and r.get("policy") in policies and not r.get("llm_error"))
total = PER_POLICY * len(policies)
ts = lambda l: datetime.datetime.strptime(" ".join(l.split()[:2]), "%Y-%m-%d %H:%M:%S")
durs, pending = [], {}
for l in lines:
    f = l.split()
    if len(f) >= 6 and f[2] in ("START", "END"):
        key = tuple(f[3:6])
        if f[2] == "START": pending[key] = ts(l)
        elif key in pending: durs.append((ts(l) - pending.pop(key)).total_seconds())
avg = sum(durs[-20:]) / len(durs[-20:]) if durs else 280
left = max(total - done, 0)
eta = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(seconds=left * avg)
bar = "#" * int(30 * done / total) + "-" * (30 - int(30 * done / total))
print(f"\nNight {n or chr(63)}: [{bar}] {done}/{total} runs ({100*done/total:.0f}%)")
print(f"about {left*avg/3600:.1f} h left, finishing around {eta:%H:%M} UTC ({(eta+datetime.timedelta(hours=3)):%H:%M} Cairo)  [avg {avg/60:.1f} min/run]")
c = collections.Counter(); ok = collections.Counter()
for r in rows:
    k = (r.get("model"), r.get("policy")); c[k] += 1
    a = r.get("aiopslab_results") or {}
    ok[k] += bool(a.get("success")) or a.get("Detection Accuracy") == "Correct"
print("\nfinished so far:")
for k in sorted(c, key=str): print(f"  {k[0]:10} {k[1]:11} {c[k]:3}/{PER_POLICY}   correct {ok[k]}")
PY' || true
  true
  ;;
watch)
  while true; do clear; date; "$0" status || true; echo; echo "(refreshes every 5 min; Ctrl-C stops watching, not the run)"; sleep 300; done
  ;;
fetch)
  ensure_running
  say "Copying results to $LOCAL_RESULTS"
  mkdir -p "$LOCAL_RESULTS"
  # pack on the host, move it in 1 MB pieces, each checksum-verified and retried
  # (one big SSM reply once came back empty and fetch silently kept the old copy)
  LIST=$(host 300 'cd ~ && tar czf /tmp/results.tgz results && rm -f /tmp/results.part.* && split -b 1000000 /tmp/results.tgz /tmp/results.part. && md5sum /tmp/results.tgz /tmp/results.part.*')
  WANT=$(echo "$LIST" | awk '$2=="/tmp/results.tgz"{print $1}')
  : > "$TMP/results.tgz"
  for f in $(echo "$LIST" | awk '$2 ~ /results.part/{print $2}'); do
    PW=$(echo "$LIST" | awk -v f="$f" '$2==f{print $1}'); ok=
    for try in 1 2 3; do
      host 300 "echo BEGIN_B64; base64 $f; echo END_B64" | sed -n '/BEGIN_B64/,/END_B64/p' | sed '1d;$d' | tr -d '\r' | base64 -d > "$TMP/part" 2>/dev/null
      PG=$(md5 -q "$TMP/part" 2>/dev/null || md5sum "$TMP/part" | cut -d' ' -f1)
      [ "$PG" = "$PW" ] && { ok=1; break; }
    done
    [ -n "$ok" ] || { echo "FETCH FAILED: piece $f did not arrive intact after 3 tries; local results NOT changed"; exit 1; }
    cat "$TMP/part" >> "$TMP/results.tgz"; printf .
  done; echo
  GOT=$(md5 -q "$TMP/results.tgz" 2>/dev/null || md5sum "$TMP/results.tgz" | cut -d' ' -f1)
  [ -n "$WANT" ] && [ "$GOT" = "$WANT" ] || { echo "FETCH FAILED: checksum mismatch; local results NOT changed"; exit 1; }
  tar xzf "$TMP/results.tgz" -C "$LOCAL_RESULTS"
  N=$(wc -l < "$LOCAL_RESULTS/results/full_rq1.jsonl" | tr -d ' ')
  echo "DONE (checksum verified): $N runs in $LOCAL_RESULTS/results/full_rq1.jsonl"
  ;;


table)
  git -C "$REPO" show "$BRANCH:ic/bench/analyze.py" > "$TMP/analyze.py"
  python3 "$TMP/analyze.py" "$LOCAL_RESULTS/results/full_rq1.jsonl"
  ;;
pause)
  pkill -f ic-bench-watch 2>/dev/null || true          # no "stopped early" alert for a deliberate pause
  ID=$(instance_id)
  if [ -n "$ID" ] && [ "$ID" != "None" ] && [ "$(state "$ID")" = "running" ]; then
    say "Stopping the run on AWS and cleaning up (about 1-2 min)"
    host 300 'pkill -f run_chunk.sh; pkill -f "ic.bench.run_aiopslab"; sleep 3; pkill -f "kubectl port-forward"
for ns in test-hotel-reservation test-social-network astronomy-shop; do kubectl delete ns $ns --ignore-not-found --wait=true --timeout=180s >/dev/null 2>&1; done
helm uninstall prometheus -n observe >/dev/null 2>&1
kubectl delete ns observe openebs --ignore-not-found --wait=true --timeout=180s >/dev/null 2>&1
echo "$(date -u "+%F %T") PAUSED by user" >> ~/results/full_progress.txt; echo CLEAN_OK' | grep -q CLEAN_OK && echo "cleaned up: no half-finished run left behind"
  fi
  pkill -f "ic-bench-tunnel" 2>/dev/null || true
  pkill -x caffeinate 2>/dev/null || true; pkill -x ollama 2>/dev/null || true
  [ -n "$ID" ] && [ "$ID" != "None" ] && aws_ ec2 stop-instances --instance-ids "$ID" >/dev/null && echo "AWS machine stopping (disk kept, ~\$0.40/day)"
  echo "laptop: tunnel, model server and keep-awake stopped"
  say "PAUSED. To continue: run the same night command again (finished runs are skipped)."
  ;;
down)
  "$0" fetch || true
  "$0" pause || true
  say "Deleting the AWS stack"
  aws_ cloudformation delete-stack --stack-name "$STACK"; aws_ cloudformation wait stack-delete-complete --stack-name "$STACK"
  echo "deleted — \$0/day. Results are in $LOCAL_RESULTS"
  ;;
*) sed -n '2,16p' "$0" ;;
esac
