#!/bin/bash
# Incident Commander — RQ1 benchmark runbook (one command per step).
#
#   ~/ic-bench.sh up        first time only (~25 min): AWS machine + Kubernetes cluster + code
#   ~/ic-bench.sh night 1   qwen3:8b — ours, random, run-everything   (~13 h)
#   ~/ic-bench.sh night 2   qwen3:8b — plain LLM, LLM-chooser          (~13 h)
#   ~/ic-bench.sh night 3   qwen3:14b — plain LLM, ours                (~14 h, optional)
#   ~/ic-bench.sh status    progress, any time (also safe while a night runs)
#   ~/ic-bench.sh fetch     copy results to the laptop
#   ~/ic-bench.sh table     results table (after fetch)
#   ~/ic-bench.sh pause     stop tunnel/model/keep-awake and stop the AWS machine (disk kept)
#   ~/ic-bench.sh down      fetch, then delete the AWS stack ($0/day)
#
# Nights are resumable: a run already finished is skipped, so re-running a night
# after an interruption just continues. Keep the laptop plugged in, online, lid open.
set -euo pipefail

REPO="/Users/shahy/Desktop/Incident commander"
BRANCH="research/voi-benchmark"
PROFILE=ic-bench; REGION=us-east-1; STACK=ic-bench
EMAIL="you@example.com"
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
  aws_ ssm start-session --target "$ID" --document-name AWS-StartNonInteractiveCommand \
    --parameters "$PARAMS" < <(sleep "$T") 2>&1 | perl -e "alarm $T; print while <STDIN>" \
    | grep -v -e "^Starting session" -e "^Exiting session" -e '^\s*$' || true
}
host() { printf '%s\n' "$2" > "$TMP/s.sh"; onhost "$1" "$TMP/s.sh"; }

wait_ssm() {
  printf 'waiting for the machine'
  for _ in $(seq 1 60); do host 30 'echo SSM_READY' | grep -q SSM_READY && { echo " ready"; return; }; printf .; sleep 10; done
  echo; echo "machine did not answer — try again in a few minutes"; exit 1
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
PROBLEMS=$(~/.local/bin/poetry run python -c "from aiopslab.orchestrator.problems.registry import ProblemRegistry as R; print(' '.join(i for i in R().get_problem_ids() if 'mitigation' not in i))" 2>/dev/null | tail -1)
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
for pol in $POLICIES; do for p in $PROBLEMS; do
  done_already "$p" "$pol" && continue
  if ! curl -sf -m 5 http://127.0.0.1:11434/api/version >/dev/null; then
    echo "$(date -u '+%F %T') STOPPED: tunnel to the laptop is down" >> "$PROG"; exit 1; fi
  echo "$(date -u '+%F %T') START $MODEL $pol $p" >> "$PROG"
  timeout 2700 ~/.local/bin/poetry run python -m ic.bench.run_aiopslab --policy "$pol" --problems "$p" \
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

start_laptop_side() {   # start_laptop_side <model>
  say "Laptop: model server, tunnel, keep-awake"
  pgrep -x ollama >/dev/null || (OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 nohup ollama serve > "$HOME/.ollama/logs/serve-bench.log" 2>&1 &)
  for _ in $(seq 1 20); do curl -sf 127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
  ollama list | grep -q "^$1" || { echo "model $1 is not downloaded: ollama pull $1"; exit 1; }
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
    *) echo "usage: ~/ic-bench.sh night 1|2|3"; exit 1;;
  esac
  ensure_running; sync_code; start_laptop_side "$MODEL"
  say "Starting night $2: $MODEL — $POLICIES"
  host 30 "nohup ~/run_chunk.sh $MODEL $POLICIES > ~/results/chunk_driver.log 2>&1 & sleep 3; tail -1 ~/results/full_progress.txt"
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
      *CHUNK_DONE*) osascript -e "display notification \"Night ${2:-} finished. Run: ~/ic-bench.sh pause\" with title \"Incident Commander benchmark\" sound name \"Glass\""; exit 0;;
      *STOPPED*)    osascript -e "display notification \"Night ${2:-} stopped early (tunnel down). Run the same night command again.\" with title \"Incident Commander benchmark\" sound name \"Basso\""; exit 0;;
    esac
  done
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
  *CHUNK_DONE*) echo ">>> NIGHT FINISHED. Next: ~/ic-bench.sh pause  (then the next night when you can)";;
  *STOPPED*)    echo ">>> STOPPED EARLY: $LAST. Run the same night command again; it continues where it left off.";;
  *PAUSED*)     echo ">>> PAUSED. Run the same night command again to continue.";;
  "")           echo ">>> nothing started yet";;
  *)            echo ">>> RUNNING. Last step: $LAST";;
esac
python3 - <<"PY"
import json, collections, os, re, datetime
NIGHTS = {("qwen3:8b", frozenset({"voi", "random", "exhaustive"})): 1,
          ("qwen3:8b", frozenset({"react", "llm_choose"})): 2,
          ("qwen3:14b", frozenset({"react", "voi"})): 3}
PER_POLICY = 75
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
for k in sorted(c, key=str): print(f"  {k[0]:10} {k[1]:11} {c[k]:3}/75   correct {ok[k]}")
PY' || true
  true
  ;;
fetch)
  ensure_running
  say "Copying results to $LOCAL_RESULTS"
  mkdir -p "$LOCAL_RESULTS"
  host 300 'cd ~ && echo BEGIN_B64 && tar czf - results 2>/dev/null | base64 -w0 && echo && echo END_B64' \
    | sed -n '/BEGIN_B64/,/END_B64/p' | sed '1d;$d' | tr -d '\r\n' | base64 -d | tar xzf - -C "$LOCAL_RESULTS"
  N=$(wc -l < "$LOCAL_RESULTS/results/full_rq1.jsonl" | tr -d ' ')
  echo "DONE: $N runs copied to $LOCAL_RESULTS"
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
