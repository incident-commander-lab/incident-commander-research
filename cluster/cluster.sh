#!/bin/bash
# The mentor-scale test bed, locally (k3d). Spec: .plans/mentor-scale-testbed-spec.md
#
#   cluster/cluster.sh up       create the cluster, monitoring, dashboards, shop + assistant
#   cluster/cluster.sh ui       Grafana http://localhost:3401 (port-forward) · shop http://localhost:3400
#   cluster/cluster.sh load trace [peak] [hours] | load const [users] [minutes]   Locust inside the cluster
#   cluster/cluster.sh status   nodes, pods, last load summary
#   cluster/cluster.sh reset    fresh pods + cleared events before a session
#   cluster/cluster.sh stop | start | down      pause / resume / delete the cluster
#
# Needs: Docker Desktop, k3d, kubectl, helm, Ollama on the Mac (the assistant's model locally).
set -euo pipefail
cd "$(dirname "$0")/.."
C=ic
# Always talk to OUR cluster, never whatever kubectl currently points at (Docker Desktop resets the
# current context to its own Kubernetes, which runs other projects).
kubectl() { command kubectl --context "k3d-$C" "$@"; }
helm() { command helm --kube-context "k3d-$C" "$@"; }

dashboards() {
  python3 cluster/monitoring/make_dashboards.py >/dev/null
  for f in cluster/monitoring/dashboards/*.json; do
    n=$(basename "$f" .json)
    kubectl -n monitoring create configmap "dash-$n" --from-file="$n.json=$f" --dry-run=client -o yaml \
      | kubectl label --local -f - grafana_dashboard=1 -o yaml | kubectl apply -f - >/dev/null
  done
}

case "${1:-}" in
up)
  k3d cluster list "$C" >/dev/null 2>&1 || k3d cluster create --config cluster/k3d/cluster.yaml
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo update >/dev/null
  kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  helm upgrade --install prometheus prometheus-community/prometheus --version 29.37.0 -n monitoring \
    -f cluster/monitoring/prometheus-values.yaml --wait --timeout 10m >/dev/null
  helm upgrade --install grafana grafana/grafana --version 10.5.15 -n monitoring \
    -f cluster/monitoring/grafana-values.yaml --wait --timeout 10m >/dev/null
  dashboards
  (cd cluster/assistant && docker build -q -t ic/assistant:1.0.0 . >/dev/null) && k3d image import ic/assistant:1.0.0 -c "$C" >/dev/null
  curl -sf -m 5 localhost:11434/api/tags >/dev/null || { nohup ollama serve >/dev/null 2>&1 & sleep 4; }
  ollama show qwen3:1.7b >/dev/null 2>&1 || ollama pull qwen3:1.7b
  kubectl apply -f cluster/k8s/shop.yaml >/dev/null
  (cd cluster/chaos && docker build -q -t ic/chaos:1.0.0 . >/dev/null) && k3d image import ic/chaos:1.0.0 -c "$C" >/dev/null
  (cd cluster/load/replay && docker build -q -t ic/replay:1.0.0 . >/dev/null) && k3d image import ic/replay:1.0.0 -c "$C" >/dev/null
  kubectl create namespace load --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n load create configmap replay-logs --from-file=cluster/load/logs/ --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n load create configmap attack-payloads --from-file=cluster/load/attacks/payloads.json --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl apply -f cluster/k8s/traefik-access-log.yaml >/dev/null
  kubectl apply -f cluster/k8s/chaos.yaml >/dev/null
  docker build -q -f cluster/agent/Dockerfile -t ic/cluster-agent:1.0.0 . >/dev/null && k3d image import ic/cluster-agent:1.0.0 -c "$C" >/dev/null
  kubectl create namespace agent --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n agent create configmap opa-policy --from-file=policy.rego=cluster/agent/policy.rego --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl apply -f cluster/k8s/agent.yaml >/dev/null
  kubectl -n shop rollout status deploy/juice-shop --timeout=10m >/dev/null
  kubectl -n shop rollout status deploy/assistant --timeout=5m >/dev/null
  echo "up. Shop: http://localhost:3400   Grafana: cluster/cluster.sh ui"
  ;;
ui)
  pkill -f "port-forward svc/grafana 3401" 2>/dev/null || true
  nohup kubectl -n monitoring port-forward svc/grafana 3401:80 >/dev/null 2>&1 &
  sleep 3; echo "Grafana http://localhost:3401/d/ic-cluster  and  /d/ic-shop   (shop: http://localhost:3400)"
  open "http://localhost:3401/d/ic-cluster" 2>/dev/null || true
  ;;
load)
  # load trace [peak-users] [trace-hours]   real daily cycle (German Wikipedia, 1 day -> 1 hour)
  # load const [users] [minutes]            constant load (also what the CPU-surge fault uses)
  MODE=${2:-trace}
  kubectl create namespace load --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n load create configmap locustfile --from-file=cluster/load/locustfile.py --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n load create configmap traces --from-file=cluster/load/traces/ --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  if [ "$MODE" = trace ]; then
    U=${3:-60}; H=${4:-24}; M=$(python3 -c "print(int($H * 3600 / 24 / 60) + 2)"); T=/traces/wikipedia-de-hourly-2026-09-28.json; N=locust-trace
  else
    U=${3:-60}; M=${4:-5}; H=0; T=""; N=locust-const
  fi
  kubectl -n load delete job "$N" --ignore-not-found >/dev/null
  sed -e "s/NAME/$N/" -e "s/\"USERS\"/\"$U\"/g" -e "s/MINUTESm/${M}m/" -e "s|\"TRACE\"|\"$T\"|" \
      -e "s/\"TRACEHOURS\"/\"$H\"/" cluster/load/job.yaml | kubectl apply -f - >/dev/null
  echo "load ($MODE): $U users$( [ "$MODE" = trace ] && echo " at the busiest hour, $H trace hours") for about $M min"
  ;;
status)
  kubectl get nodes -L role; kubectl get pods -A -o wide --no-headers | awk '$1 ~ /shop|monitoring|load|ai|pulse/ {print $1, $2, $4, $8}'
  for j in locust-trace locust-const; do kubectl -n load logs job/$j --tail=12 2>/dev/null || true; done
  ;;
reset)
  # before a session: fresh pods (restart counters 0) and no stale Warning events from earlier runs,
  # so the agent's first evidence describes now, not the last stop/start of the laptop cluster
  kubectl apply -f cluster/k8s/shop.yaml >/dev/null
  kubectl -n shop rollout restart deploy/juice-shop deploy/assistant >/dev/null
  kubectl -n shop rollout status deploy/juice-shop --timeout=5m >/dev/null && kubectl -n shop rollout status deploy/assistant --timeout=5m >/dev/null
  kubectl -n shop delete events --all >/dev/null
  echo "reset: fresh pods, events cleared"
  ;;
stop)  k3d cluster stop "$C" ;;
start) k3d cluster start "$C" ;;
down)  k3d cluster delete "$C" ;;
*) sed -n 2,12p "$0"; exit 1 ;;
esac
