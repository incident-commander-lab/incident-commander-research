#!/bin/bash
# The mentor-scale test bed, locally (k3d). Spec: .plans/mentor-scale-testbed-spec.md
#
#   cluster/cluster.sh up       create the cluster, monitoring, dashboards, shop + assistant
#   cluster/cluster.sh ui       Grafana http://localhost:3401 (port-forward) · shop http://localhost:3400
#   cluster/cluster.sh load [users] [minutes]   a Locust run inside the cluster (default 60 users, 5 min)
#   cluster/cluster.sh status   nodes, pods, last load summary
#   cluster/cluster.sh stop | start | down      pause / resume / delete the cluster
#
# Needs: Docker Desktop, k3d, kubectl, helm, Ollama on the Mac (the assistant's model locally).
set -euo pipefail
cd "$(dirname "$0")/.."
C=ic

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
  U=${2:-60}; M=${3:-5}
  kubectl create namespace load --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n load create configmap locustfile --from-file=cluster/load/locustfile.py --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n load delete job locust-run --ignore-not-found >/dev/null
  sed -e "s/\"-u\", \"60\"/\"-u\", \"$U\"/" -e "s/\"-t\", \"5m\"/\"-t\", \"${M}m\"/" cluster/load/job.yaml | kubectl apply -f - >/dev/null
  echo "load: $U users for $M min (watch Grafana; summary: cluster/cluster.sh status)"
  ;;
status)
  kubectl get nodes -L role; kubectl get pods -A -o wide --no-headers | awk '$1 ~ /shop|monitoring|load|ai|pulse/ {print $1, $2, $4, $8}'
  kubectl -n load logs job/locust-run --tail=25 2>/dev/null || true
  ;;
stop)  k3d cluster stop "$C" ;;
start) k3d cluster start "$C" ;;
down)  k3d cluster delete "$C" ;;
*) sed -n 2,12p "$0"; exit 1 ;;
esac
