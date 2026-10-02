#!/bin/bash
# Expose the laptop's Ollama to the benchmark host, without opening any port.
#
#   laptop 127.0.0.1:11434 (Ollama)  <──ssh -R──  host 127.0.0.1:11434
#
# SSH runs inside an SSM session (document AWS-StartSSHSession), so neither
# machine accepts inbound connections. On the host, the key is restricted to
# this one reverse forward (authorized_keys: restrict, port-forwarding,
# permitlisten="localhost:11434", command="echo tunnel-only").
#
# Usage:  infra/tunnel.sh <instance-id>        (Ctrl-C to close)
# Needs:  ollama serving locally, the Session Manager plugin, AWS profile ic-bench,
#         and ~/.ssh/ic-bench-tunnel registered on the host.
set -euo pipefail

INSTANCE="${1:?usage: infra/tunnel.sh <instance-id>}"
PROFILE="${AWS_PROFILE_BENCH:-ic-bench}"
REGION="${AWS_REGION_BENCH:-us-east-1}"
KEY="${TUNNEL_KEY:-$HOME/.ssh/ic-bench-tunnel}"

curl -sf http://127.0.0.1:11434/api/version >/dev/null \
  || { echo "Ollama is not running on 127.0.0.1:11434 (start it: ollama serve)"; exit 1; }

exec ssh -i "$KEY" -N \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=4 \
  -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts_ic_bench" \
  -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p --profile $PROFILE --region $REGION" \
  -R 11434:127.0.0.1:11434 \
  "ubuntu@$INSTANCE"
