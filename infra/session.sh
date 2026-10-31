#!/bin/bash
# One GPU session host (eu-north-1, Spot) for the RQ3 Compose test bed, launched by the account
# owner's user and handed to a collaborator, who runs testbed/run-session.sh on it through Session
# Manager. Nothing is public; the machine terminates itself after the session's hours.
#
#   infra/session.sh up [hours] [owner]   create the machine (default 8 h, owner nourseen); refuses above $5
#                                         or below the $15 credit floor; tries g4dn in 1a/1b/1c, then g5
#   infra/session.sh deploy [git-ref]     copy the code (default: research/eval/rq3-recordings) and wait until
#                                         Ollama and qwen3:8b are ready
#   infra/session.sh status               state, time left, the model, the commands the collaborator runs
#   infra/session.sh run <script> [s]     a bash script as root on the host (checks, fixes)
#   infra/session.sh results              what the host has uploaded so far (session-results/ in the bucket)
#   infra/session.sh down                 delete the stack now (the machine and its disk are gone)
#
# One-time, as root: deploy infra/session-iam.yaml (creates the ic-gpu-session-host role).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE=${IC_AWS_PROFILE:-ic-bench}; REGION=eu-north-1; CAP=5; BUCKET=ic-recordings-374617280285
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }
say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
stack() { aws_ cloudformation describe-stacks --query "Stacks[?starts_with(StackName,'ic-gpu-sess-') && StackStatus!='DELETE_COMPLETE'].StackName|[0]" --output text 2>/dev/null | grep -v None || true; }
out()   { aws_ cloudformation describe-stacks --stack-name "$(stack)" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue|[0]" --output text; }

onhost() {   # onhost <timeout> <script file>: root on the host through Session Manager
  local T=$1 F=$2 ID B64 P
  ID=$(out InstanceId); B64=$(base64 < "$F" | tr -d '\n')
  P=$(python3 -c 'import json,sys; print(json.dumps({"command":["bash -c \"echo %s | base64 -d | sudo -i bash\"" % sys.argv[1]]}))' "$B64")
  perl -e 'alarm shift; exec @ARGV' "$T" aws --profile "$PROFILE" --region "$REGION" ssm start-session \
    --target "$ID" --document-name AWS-StartNonInteractiveCommand --parameters "$P" < <(sleep "$T") 2>&1 \
    | grep -v -e "^Starting session" -e "^Exiting session" -e '^\s*$' || true
}
host() { printf '%s\n' "$2" > "$TMP/s.sh"; onhost "$1" "$TMP/s.sh"; }

case "${1:-}" in
up)
  H=${2:-8}; OWNER=${3:-nourseen}
  [ -n "$(stack)" ] && { echo "a session host already exists: $(stack)"; exit 1; }
  aws_ iam get-instance-profile --instance-profile-name ic-gpu-session-host >/dev/null 2>&1 \
    || { echo "STOP: the ic-gpu-session-host role does not exist. As root, deploy infra/session-iam.yaml in $REGION first."; exit 1; }
  P=$(aws_ ec2 describe-spot-price-history --instance-types g4dn.xlarge g5.xlarge --product-descriptions Linux/UNIX \
        --start-time "$(date -u +%FT%TZ)" --query 'SpotPriceHistory[].[InstanceType,SpotPrice]' --output text \
      | python3 -c 'import sys
m=max(float(l.split()[1]) for l in sys.stdin); print(round(m + 150*0.0836/730, 4))')
  COST=$(python3 -c "print(round($P * $H, 2))")
  echo "estimate: \$$P/h (dearest fallback + disk) x ${H} h = \$$COST (cap \$$CAP)"
  python3 -c "import sys; sys.exit(0 if $COST <= $CAP else 1)" || { echo "STOP: above the \$$CAP cap"; exit 1; }
  LEFT=$(aws --profile "$PROFILE" --region us-east-1 freetier get-account-plan-state --query 'accountPlanRemainingCredits.amount' --output text)
  echo "credits left: \$$LEFT"
  python3 -c "import sys; sys.exit(0 if float('$LEFT') - float('$COST') >= 15 else 1)" || { echo "STOP: would go below the \$15 credit floor"; exit 1; }
  SG=$(aws_ ec2 describe-security-groups --filters Name=group-name,Values=default --query 'SecurityGroups[0].GroupId' --output text)
  for TRY in g4dn.xlarge:eu-north-1a g4dn.xlarge:eu-north-1b g4dn.xlarge:eu-north-1c g5.xlarge:eu-north-1b g5.xlarge:eu-north-1c; do
    TYPE=${TRY%%:*}; AZ=${TRY#*:}
    SUB=$(aws_ ec2 describe-subnets --filters Name=default-for-az,Values=true Name=availability-zone,Values=$AZ --query 'Subnets[0].SubnetId' --output text)
    NAME="ic-gpu-sess-$(date +%m%d-%H%M%S)"
    say "Creating $NAME: $TYPE in $AZ (Spot, owner $OWNER, self-terminates after $H h)"
    aws_ cloudformation create-stack --stack-name "$NAME" --template-body "file://$REPO/infra/session-stack.yaml" \
      --tags Key=project,Value=incident-commander-gpu \
      --parameters ParameterKey=SessionHours,ParameterValue="$H" ParameterKey=Owner,ParameterValue="$OWNER" \
                   ParameterKey=Bucket,ParameterValue="$BUCKET" ParameterKey=SubnetId,ParameterValue="$SUB" \
                   ParameterKey=SecurityGroupId,ParameterValue="$SG" ParameterKey=InstanceType,ParameterValue="$TYPE" >/dev/null
    if aws_ cloudformation wait stack-create-complete --stack-name "$NAME" 2>/dev/null; then
      echo "created with $TYPE in $AZ. Next: infra/session.sh deploy"; exit 0
    fi
    echo "failed: $(aws_ cloudformation describe-stack-events --stack-name "$NAME" --query "StackEvents[?ResourceStatus=='CREATE_FAILED' && !contains(ResourceStatusReason,'cancelled')].ResourceStatusReason|[0]" --output text | cut -c1-170)"
    aws_ cloudformation delete-stack --stack-name "$NAME"; aws_ cloudformation wait stack-delete-complete --stack-name "$NAME" || true
  done
  exit 1
  ;;
deploy)
  REF=${2:-research/eval/rq3-recordings}
  [ -n "$(stack)" ] || { echo "no session host: infra/session.sh up"; exit 1; }
  git -C "$REPO" rev-parse --verify -q "$REF^{commit}" >/dev/null || { echo "unknown git ref $REF (git fetch research first?)"; exit 1; }
  say "Waiting for the host to answer"
  for i in $(seq 1 60); do host 40 'echo SSM_READY' | grep -q SSM_READY && break; printf .; sleep 10; done; echo
  say "Copying the code ($REF: $(git -C "$REPO" rev-parse --short "$REF"))"
  git -C "$REPO" archive "$REF" -- ic testbed pyproject.toml docs/paper | gzip -9 | base64 | tr -d '\n' > "$TMP/code.b64"
  split -b 60000 "$TMP/code.b64" "$TMP/c_"
  n=0; for c in "$TMP"/c_*; do n=$((n+1)); { [ $n = 1 ] && echo 'rm -f /root/code.b64'; echo "printf %s '$(cat "$c")' >> /root/code.b64"; } > "$TMP/s.sh"; onhost 120 "$TMP/s.sh" >/dev/null; printf .; done; echo
  want=$(shasum -a 256 < "$TMP/code.b64" | cut -c1-16)
  host 180 "test \"\$(sha256sum < /root/code.b64 | cut -c1-16)\" = $want && mkdir -p /home/ubuntu/ic && base64 -d /root/code.b64 | tar xzf - -C /home/ubuntu/ic && mkdir -p /home/ubuntu/ic/testbed/recordings && chown -R ubuntu:ubuntu /home/ubuntu/ic && echo CODE_OK $want"
  say "Waiting for Ollama and qwen3:8b (about 10 min after the machine starts)"
  for i in $(seq 1 90); do host 40 'test -f /var/lib/ic-session-ready && echo READY' | grep -q READY && break; printf .; sleep 15; done; echo
  host 120 'nvidia-smi -L; docker compose version; docker exec ollama ollama list; curl -s localhost:11434/api/tags | head -c 200; echo; df -h / | tail -1'
  ;;
status)
  S=$(stack); [ -n "$S" ] || { echo "no session host (nothing is costing money)"; exit 0; }
  ID=$(out InstanceId); OWNER=$(out Owner)
  echo "stack: $S  instance: $ID  owner: $OWNER"
  aws_ ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].[InstanceType,State.Name,Placement.AvailabilityZone,LaunchTime]' --output text
  host 90 'echo "shutdown: $(date -u -d @$(( $(grep USEC /run/systemd/shutdown/scheduled | cut -d= -f2) / 1000000 )) +%F\ %H:%M) UTC"; test -f /var/lib/ic-session-ready && echo "ready: Ollama + qwen3:8b" || echo "still starting"; docker exec ollama ollama list 2>/dev/null | tail -2; ls /home/ubuntu/ic/testbed/sessions 2>/dev/null | tail -5'
  cat <<EOT

$OWNER connects with her own profile (Session Manager plugin installed):
  aws ssm start-session --target $ID --region $REGION --profile <her profile>
  sudo -iu ubuntu
  cd ic/testbed
  aws s3 sync s3://$BUCKET /data/s3 --exclude 'session-results/*'        # the recordings she needs
  ./run-session.sh rule 1 <seed> <seed>.ndjson                          # a session; see docs/paper/RQ3_PROTOCOL.md
Session outputs are copied to s3://$BUCKET/session-results/$S/ every 5 minutes.
EOT
  ;;
run)
  [ -f "${2:-}" ] || { echo "usage: infra/session.sh run <script> [seconds]"; exit 1; }
  onhost "${3:-300}" "$2"
  ;;
results)
  S=$(stack); [ -n "$S" ] || { echo "no session host"; exit 1; }
  host 90 "aws s3 ls s3://$BUCKET/session-results/$S/ --recursive --human-readable | tail -20"
  ;;
down)
  S=$(stack); [ -n "$S" ] || { echo "nothing to delete"; exit 0; }
  aws_ cloudformation delete-stack --stack-name "$S" && aws_ cloudformation wait stack-delete-complete --stack-name "$S" && echo "deleted $S (the machine and its disk are gone; uploaded results stay in the bucket)"
  ;;
*) sed -n 2,16p "$0"; exit 1 ;;
esac
