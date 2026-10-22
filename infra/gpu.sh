#!/bin/bash
# The mentor-scale test bed on AWS (eu-north-1, Spot): one command per step, all from the laptop.
# Nothing heavy runs locally; every machine terminates itself after the session's hours.
#
#   infra/gpu.sh up [hours]   create the 3 machines (ops, apps, gpu) - refuses above $5 or below the credit floor
#                             (CORE=spot infra/gpu.sh up: ops/apps on Spot too, cheaper but can be reclaimed)
#   infra/gpu.sh deploy       copy the code, build images on the ops machine, install everything
#   infra/gpu.sh status       nodes, pods, GPU, time left, cost so far
#   infra/gpu.sh ui           Grafana http://localhost:3401 and the shop http://localhost:3400 through Session Manager
#   infra/gpu.sh run <script> [s] run a bash script on the ops machine (kubectl/helm there; over 900 s: detached)
#   infra/gpu.sh down         delete the stack now (machines terminate, disks deleted)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE=ic-bench; REGION=eu-north-1; CAP=5
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }
say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
stack() { aws_ cloudformation describe-stacks --query "Stacks[?starts_with(StackName,'ic-gpu-') && StackName!='ic-gpu-recorder' && StackStatus!='DELETE_COMPLETE'].StackName|[0]" --output text 2>/dev/null | grep -v None || true; }
out()   { aws_ cloudformation describe-stacks --stack-name "$(stack)" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue|[0]" --output text; }

onhost() {   # onhost <timeout> <script file>: run as root on the ops machine via Session Manager (it logs in as ssm-user)
  local T=$1 F=$2 ID B64 P
  ID=$(out OpsId); B64=$(base64 < "$F" | tr -d '\n')
  P=$(python3 -c 'import json,sys; print(json.dumps({"command":["bash -c \"echo %s | base64 -d | sudo -i bash\"" % sys.argv[1]]}))' "$B64")
  perl -e 'alarm shift; exec @ARGV' "$T" aws --profile "$PROFILE" --region "$REGION" ssm start-session \
    --target "$ID" --document-name AWS-StartNonInteractiveCommand --parameters "$P" < <(sleep "$T") 2>&1 \
    | grep -v -e "^Starting session" -e "^Exiting session" -e '^\s*$' || true
}
host() { printf '%s\n' "$2" > "$TMP/s.sh"; onhost "$1" "$TMP/s.sh"; }

detached() { # detached <max-seconds> <script file>: run on ops in the background and follow its log.
  # Session Manager ends a session after 20 min without client input (output does not count), so a
  # long script must not live inside one; each poll below is a short session of its own.
  local T=$1 F=$2 N L
  N="job-$(date +%s)"; L="/root/$N.log"
  { echo "cat > /root/$N.sh <<'IC_JOB_EOF'"; cat "$F"; echo "IC_JOB_EOF"
    echo "nohup bash -c 'bash /root/$N.sh; echo IC_JOB_EXIT $?' > $L 2>&1 < /dev/null & echo started $N"; } > "$TMP/d.sh"
  onhost 60 "$TMP/d.sh"
  local seen=0 end=$(( $(date +%s) + T ))
  while [ "$(date +%s)" -lt "$end" ]; do
    sleep 30
    host 40 "tail -n +$((seen + 1)) $L" > "$TMP/new.txt" || true
    [ -s "$TMP/new.txt" ] && { cat "$TMP/new.txt"; seen=$((seen + $(wc -l < "$TMP/new.txt"))); }
    grep -q "^IC_JOB_EXIT" "$TMP/new.txt" && return 0
  done
  echo "(stopped following after ${T}s; the job keeps running: infra/gpu.sh run-log $N)"
}

CORE=${CORE:-ondemand}   # ops + apps: ondemand (default, survives the session) or spot (cheaper, can be reclaimed)
hourly() {   # $/h of the three machines + disks: the dearest GPU fallback at its worst-AZ Spot price,
             # ops + apps at Spot or the eu-north-1 on-demand list price (t3.large $0.0864, m7i.xlarge $0.2142, Oct 2026)
  aws_ ec2 describe-spot-price-history --instance-types g6.xlarge g5.xlarge g4dn.xlarge m7i.xlarge t3.large --product-descriptions Linux/UNIX \
    --start-time "$(date -u +%FT%TZ)" --query 'SpotPriceHistory[].[InstanceType,SpotPrice]' --output text \
  | python3 -c 'import sys
best={}
for t,p in (l.split() for l in sys.stdin): best[t]=max(best.get(t,0),float(p))
gpu=max(best.get(t,0) for t in ("g6.xlarge","g5.xlarge","g4dn.xlarge"))
core=best["t3.large"]+best["m7i.xlarge"] if sys.argv[1]=="spot" else 0.0864+0.2142
print(round(gpu + core + 160*0.0836/730, 4))' "$CORE"
}

guard() {    # credits left (exact, Free Tier API) must stay above the floor after this session
  local left; left=$(aws --profile "$PROFILE" --region us-east-1 freetier get-account-plan-state --query 'accountPlanRemainingCredits.amount' --output text)
  echo "credits left: \$$left"
  python3 -c "import sys; sys.exit(0 if float('$left') - float('$1') >= 15 else 1)" || { echo "STOP: would go below the \$15 credit floor"; exit 1; }
}

case "${1:-}" in
up)
  H=${2:-2}
  [ -n "$(stack)" ] && { echo "a session stack already exists: $(stack)"; exit 1; }
  P=$(hourly); COST=$(python3 -c "print(round($P * $H, 2))")
  echo "estimate: \$$P/h x ${H} h = \$$COST (cap \$$CAP)"
  python3 -c "import sys; sys.exit(0 if $COST <= $CAP else 1)" || { echo "STOP: above the \$$CAP session cap"; exit 1; }
  guard "$COST"
  SG=$(aws_ ec2 describe-security-groups --filters Name=group-name,Values=default --query 'SecurityGroups[0].GroupId' --output text)
  # Spot GPU capacity varies: try the planned L4 first, then A10G, then T4, in each zone that offers it
  for TRY in g6.xlarge:eu-north-1a g6.xlarge:eu-north-1b g5.xlarge:eu-north-1b g5.xlarge:eu-north-1c \
             g4dn.xlarge:eu-north-1a g4dn.xlarge:eu-north-1b g4dn.xlarge:eu-north-1c; do
    GPU=${TRY%%:*}; AZ=${TRY#*:}
    SUB=$(aws_ ec2 describe-subnets --filters Name=default-for-az,Values=true Name=availability-zone,Values=$AZ --query 'Subnets[0].SubnetId' --output text)
    NAME="ic-gpu-$(date +%m%d-%H%M%S)"
    say "Creating $NAME: $GPU in $AZ (GPU Spot, ops/apps $CORE; self-terminates after $H h)"
    aws_ cloudformation create-stack --stack-name "$NAME" --template-body "file://$REPO/infra/gpu-stack.yaml" \
      --capabilities CAPABILITY_NAMED_IAM --tags Key=project,Value=incident-commander-gpu \
      --parameters ParameterKey=SessionHours,ParameterValue="$H" ParameterKey=K3sToken,ParameterValue="$(openssl rand -hex 24)" \
                   ParameterKey=SubnetId,ParameterValue="$SUB" ParameterKey=SecurityGroupId,ParameterValue="$SG" \
                   ParameterKey=GpuInstanceType,ParameterValue="$GPU" ParameterKey=CoreMarket,ParameterValue="$CORE" >/dev/null
    if aws_ cloudformation wait stack-create-complete --stack-name "$NAME" 2>/dev/null; then
      echo "created with $GPU in $AZ. Next: infra/gpu.sh deploy"; exit 0
    fi
    echo "failed: $(aws_ cloudformation describe-stack-events --stack-name "$NAME" --query "StackEvents[?ResourceStatus=='CREATE_FAILED' && !contains(ResourceStatusReason,'cancelled')].ResourceStatusReason|[0]" --output text | cut -c1-160)"
    aws_ cloudformation delete-stack --stack-name "$NAME"; aws_ cloudformation wait stack-delete-complete --stack-name "$NAME" || true
  done
  exit 1
  ;;
deploy)
  [ -n "$(stack)" ] || { echo "no session: infra/gpu.sh up"; exit 1; }
  say "Waiting for the ops machine and the 3-node cluster"
  for i in $(seq 1 60); do host 40 'test -f /var/lib/ic-ops-ready && k3s kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready"' | tail -1 | grep -q 3 && break; printf .; sleep 15; done; echo
  say "Copying the code to the ops machine"
  git -C "$REPO" archive HEAD cluster ic | gzip -9 | base64 | tr -d '\n' > "$TMP/code.b64"
  split -b 60000 "$TMP/code.b64" "$TMP/c_"
  n=0; for c in "$TMP"/c_*; do n=$((n+1)); { [ $n = 1 ] && echo 'rm -f /root/code.b64'; echo "printf %s '$(cat "$c")' >> /root/code.b64"; } > "$TMP/s.sh"; onhost 120 "$TMP/s.sh" >/dev/null; printf .; done; echo
  want=$(shasum -a 256 < "$TMP/code.b64" | cut -c1-16)
  host 120 "test \"\$(sha256sum < /root/code.b64 | cut -c1-16)\" = $want && rm -rf /root/ic && mkdir -p /root/ic && cd /root/ic && base64 -d /root/code.b64 | tar xzf - && echo CODE_OK $want"
  say "Building images and installing (about 10 minutes)"
  cat > "$TMP/deploy.sh" <<'DEPLOY'
set -e
cd /root/ic; export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
for img in "assistant cluster/assistant" "chaos cluster/chaos" "replay cluster/load/replay"; do set -- $img
  docker build -q -t localhost:5000/ic/$1:1.0.0 $2 >/dev/null && docker push -q localhost:5000/ic/$1:1.0.0 >/dev/null; done
docker build -q -f cluster/agent/Dockerfile -t localhost:5000/ic/cluster-agent:1.0.0 . >/dev/null && docker push -q localhost:5000/ic/cluster-agent:1.0.0 >/dev/null
echo IMAGES_OK
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null; helm repo add grafana https://grafana.github.io/helm-charts >/dev/null
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin >/dev/null; helm repo add gpu-helm-charts https://nvidia.github.io/dcgm-exporter/helm-charts >/dev/null; helm repo update >/dev/null
helm upgrade --install nvdp nvdp/nvidia-device-plugin --version 0.20.1 -n kube-system -f cluster/aws/device-plugin-values.yaml --wait --timeout 10m >/dev/null && echo DEVICE_PLUGIN_OK
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - >/dev/null
helm upgrade --install dcgm gpu-helm-charts/dcgm-exporter --version 4.8.4 -n monitoring -f cluster/aws/dcgm-values.yaml --wait --timeout 10m >/dev/null && echo DCGM_OK
helm upgrade --install prometheus prometheus-community/prometheus --version 29.37.0 -n monitoring -f cluster/monitoring/prometheus-values.yaml --wait --timeout 10m >/dev/null
helm upgrade --install grafana grafana/grafana --version 10.5.15 -n monitoring -f cluster/monitoring/grafana-values.yaml --wait --timeout 10m >/dev/null && echo MONITORING_OK
for f in cluster/monitoring/dashboards/*.json; do n=$(basename $f .json)
  kubectl -n monitoring create configmap dash-$n --from-file=$n.json=$f --dry-run=client -o yaml | kubectl label --local -f - grafana_dashboard=1 -o yaml | kubectl apply -f - >/dev/null; done
kubectl apply -f cluster/k8s/ai.yaml >/dev/null
kubectl -n ai rollout status deploy/ollama-agent --timeout=20m >/dev/null && echo AGENT_MODEL_ON_GPU
R=registry.ic.local:5000
# the laptop manifests, pointed at the GPU node's model servers and the session registry
sed -e "s#image: ic/assistant:1.0.0#image: $R/ic/assistant:1.0.0#" -e 's#http://host.k3d.internal:11434"#http://ollama-chat.ai.svc:11434"#' -e 's#"qwen3:1.7b"#"qwen3:4b-q4_K_M"#' cluster/k8s/shop.yaml | kubectl apply -f - >/dev/null
kubectl create namespace load --dry-run=client -o yaml | kubectl apply -f - >/dev/null
sed -e "s#image: ic/chaos:1.0.0#image: $R/ic/chaos:1.0.0#" -e 's#value: "16384"#value: "40960"#' cluster/k8s/chaos.yaml | kubectl apply -f - >/dev/null
kubectl create namespace agent --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n agent create configmap opa-policy --from-file=policy.rego=cluster/agent/policy.rego --dry-run=client -o yaml | kubectl apply -f - >/dev/null
sed -e "s#image: ic/cluster-agent:1.0.0#image: $R/ic/cluster-agent:1.0.0#" -e 's#http://host.k3d.internal:11434/v1#http://ollama-agent.ai.svc:11434/v1#' cluster/k8s/agent.yaml | kubectl apply -f - >/dev/null
kubectl -n load create configmap locustfile --from-file=cluster/load/locustfile.py --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n load create configmap traces --from-file=cluster/load/traces/ --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n load create configmap replay-logs --from-file=cluster/load/logs/ --dry-run=client -o yaml | kubectl apply -f - >/dev/null
for d in shop/juice-shop shop/assistant chaos/chaos agent/opa agent/ic-agent ai/ollama-chat; do kubectl -n ${d%/*} rollout status deploy/${d#*/} --timeout=15m >/dev/null; done
echo DEPLOY_DONE
kubectl get pods -A -o wide --no-headers | awk '$1 ~ /shop|ai|agent|chaos|monitoring/ {print $1, $2, $4, $8}'
DEPLOY
  detached 3000 "$TMP/deploy.sh"
  ;;
status)
  S=$(stack); [ -n "$S" ] || { echo "no session running (nothing is costing money)"; exit 0; }
  echo "stack: $S  ($(aws_ cloudformation describe-stacks --stack-name "$S" --query 'Stacks[0].StackStatus' --output text))"
  host 120 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; k3s kubectl get nodes -L role --no-headers | awk "{print \$1, \$2, \$6}"; k3s kubectl get pods -A --no-headers | awk "\$1 ~ /shop|ai|agent|chaos|load/ {print \$1, \$2, \$4}"; echo "shutdown: $(date -u -d @$(( $(cat /run/systemd/shutdown/scheduled | grep USEC | cut -d= -f2) / 1000000 )) +%H:%M) UTC"'
  ;;
ui)
  ID=$(out OpsId)
  host 60 'pkill -f "port-forward svc/grafana" || true; KUBECONFIG=/etc/rancher/k3s/k3s.yaml nohup k3s kubectl -n monitoring port-forward svc/grafana 3401:80 >/dev/null 2>&1 & echo grafana-forward-on-ops'
  nohup aws --profile "$PROFILE" --region "$REGION" ssm start-session --target "$ID" --document-name AWS-StartPortForwardingSession --parameters portNumber=3401,localPortNumber=3401 >/dev/null 2>&1 &
  nohup aws --profile "$PROFILE" --region "$REGION" ssm start-session --target "$ID" --document-name AWS-StartPortForwardingSession --parameters portNumber=80,localPortNumber=3400 >/dev/null 2>&1 &
  sleep 5; echo "Grafana http://localhost:3401/d/ic-cluster  ·  shop http://localhost:3400"
  ;;
run)          # run <script> [max-seconds]: detached on ops, log followed here
  [ -f "${2:-}" ] || { echo "usage: infra/gpu.sh run <script> [max-seconds]"; exit 1; }
  if [ "${3:-900}" -le 900 ]; then onhost "${3:-900}" "$2"; else detached "$3" "$2"; fi
  ;;
run-log)      # run-log <job>: the whole log of a detached job
  host 60 "cat /root/${2:?job name}.log"
  ;;
down)
  S=$(stack); [ -n "$S" ] || { echo "nothing to delete"; exit 0; }
  aws_ cloudformation delete-stack --stack-name "$S" && aws_ cloudformation wait stack-delete-complete --stack-name "$S" && echo "deleted $S (machines terminated, disks deleted)"
  ;;
*) sed -n 2,12p "$0"; exit 1 ;;
esac
