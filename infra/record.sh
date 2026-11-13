#!/bin/bash
# The RQ3 recorder on AWS (eu-north-1, one t3.medium, ~$0.05/h): continuous recording of the live
# Wikimedia stream for the replay seeds (docs/paper/RQ3_PROTOCOL.md). Runs from any laptop with the
# AWS CLI and the Session Manager plugin; nothing runs locally.
#
#   infra/record.sh up                       create the machine (needs create rights; done once by Shahesta)
#   infra/record.sh start ['YYYY-MM-DD HH:MM']  copy the recorder, start recording; the machine stops itself
#                                            at that UTC time (disk and recordings kept)
#   infra/record.sh status                   containers, files per hour, disconnects, stop time, machine state
#   infra/record.sh stop                     stop recording (files kept on the machine)
#   infra/record.sh pause                    stop the machine (disk kept, ~$0.11/day); start resumes it
#   infra/record.sh down                     delete the machine AND its recordings (fetch first)
#
# Profile: IC_AWS_PROFILE (default ic-bench), e.g. IC_AWS_PROFILE=nour infra/record.sh status
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE=${IC_AWS_PROFILE:-ic-bench}; REGION=eu-north-1; STACK=ic-gpu-recorder   # ic-gpu*: the only names ic-gpu-stockholm may create
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }
say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
# Collaborator mode (infra/nour-recording.yaml): IC_RECORDER_OWNER=<IAM user> uses her own server, found
# by its owner tag (she has no CloudFormation rights), launched with `launch`, and uploads each finished
# hour to the recordings bucket.
OWNER=${IC_RECORDER_OWNER:-}; NAME=ic-recorder-$OWNER
BUCKET=${IC_RECORDINGS_BUCKET:-}
iid()  {
  if [ -n "$OWNER" ]; then
    aws_ ec2 describe-instances --filters "Name=tag:owner,Values=$OWNER" "Name=tag:Name,Values=$NAME" \
      "Name=instance-state-name,Values=pending,running,stopping,stopped" \
      --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || true
  else
    aws_ cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue|[0]" --output text 2>/dev/null || true
  fi
}
state() { aws_ ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo none; }

onhost() {   # onhost <timeout> <script file>: run as ubuntu on the recorder via Session Manager
  local T=$1 F=$2 B64 P
  B64=$(base64 < "$F" | tr -d '\n')
  P=$(python3 -c 'import json,sys; print(json.dumps({"command":["bash -c \"echo %s | base64 -d | sudo -iu ubuntu bash\"" % sys.argv[1]]}))' "$B64")
  perl -e 'alarm shift; exec @ARGV' "$T" aws --profile "$PROFILE" --region "$REGION" ssm start-session \
    --target "$ID" --document-name AWS-StartNonInteractiveCommand --parameters "$P" < <(sleep "$T") 2>&1 \
    | grep -v -e "^Starting session" -e "^Exiting session" -e '^\s*$' || true
}
host() { printf '%s\n' "$2" > "$TMP/s.sh"; onhost "$1" "$TMP/s.sh"; }

running() {  # start the machine if stopped, then wait until Session Manager and Docker answer
  ID=$(iid); { [ -z "$ID" ] || [ "$ID" = None ]; } && { echo "no recorder machine yet: infra/record.sh up"; exit 1; }
  local S; S=$(state "$ID")
  if [ "$S" != running ]; then
    say "Starting the recorder machine ($S -> running)"
    [ "$S" = stopping ] && aws_ ec2 wait instance-stopped --instance-ids "$ID"
    aws_ ec2 start-instances --instance-ids "$ID" >/dev/null; aws_ ec2 wait instance-running --instance-ids "$ID"
  fi
  printf 'waiting for the machine'
  for _ in $(seq 1 60); do host 30 'test -f /var/lib/ic-recorder-ready && docker info >/dev/null 2>&1 && echo READY' | grep -q READY && { echo " ready"; return; }; printf .; sleep 10; done
  echo; echo "machine did not answer - try again in a few minutes"; exit 1
}

case "${1:-status}" in
launch)   # collaborator mode: her own t3.medium, same setup as record-stack.yaml plus the AWS CLI for uploads
  [ -n "$OWNER" ] || { echo "launch needs IC_RECORDER_OWNER (use 'up' otherwise)"; exit 1; }
  [ -n "$(iid)" ] && [ "$(iid)" != None ] && { echo "recorder already exists: $(iid)"; exit 0; }
  AMI=$(aws_ ec2 describe-images --owners 099720109477 --filters \
    "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" "Name=state,Values=available" \
    --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)
  SG=$(aws_ ec2 describe-security-groups --filters Name=group-name,Values=default --query 'SecurityGroups[0].GroupId' --output text)
  SUB=$(aws_ ec2 describe-subnets --filters Name=default-for-az,Values=true Name=availability-zone,Values=eu-north-1a --query 'Subnets[0].SubnetId' --output text)
  cat > "$TMP/ud.sh" <<'UD'
#!/bin/bash
set -ux
shutdown -h +$(( 48 * 60 )) "idle backstop - record.sh start sets the real stop time"
apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 docker-buildx >/dev/null
usermod -aG docker ubuntu
snap install aws-cli --classic
touch /var/lib/ic-recorder-ready
UD
  say "Launching $NAME (${IC_INSTANCE_TYPE:-t3.medium} on-demand, Ubuntu 24.04 $AMI, eu-north-1a)"
  MSYS_NO_PATHCONV=1 aws_ ec2 run-instances ${IC_DRY_RUN:+--dry-run} --image-id "$AMI" --instance-type "${IC_INSTANCE_TYPE:-t3.medium}" --subnet-id "$SUB" --security-group-ids "$SG" \
    --iam-instance-profile Name=ic-rec-host --instance-initiated-shutdown-behavior stop \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=20,VolumeType=gp3,DeleteOnTermination=true}' \
    --user-data "file://$(cygpath -w "$TMP/ud.sh" 2>/dev/null || echo "$TMP/ud.sh")" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=owner,Value=$OWNER},{Key=Name,Value=$NAME},{Key=project,Value=incident-commander-recording}]" \
    --query 'Instances[0].InstanceId' --output text
  echo "launched. Next: infra/record.sh start 'YYYY-MM-DD HH:MM'  (UTC stop time)"
  ;;
up)
  [ -n "$(iid)" ] && [ "$(iid)" != None ] && { echo "recorder already exists: $(iid)"; exit 0; }
  SG=$(aws_ ec2 describe-security-groups --filters Name=group-name,Values=default --query 'SecurityGroups[0].GroupId' --output text)
  SUB=$(aws_ ec2 describe-subnets --filters Name=default-for-az,Values=true Name=availability-zone,Values=eu-north-1a --query 'Subnets[0].SubnetId' --output text)
  say "Creating $STACK (t3.medium on-demand, eu-north-1a)"
  aws_ cloudformation create-stack --stack-name "$STACK" --template-body "file://$REPO/infra/record-stack.yaml" \
    --capabilities CAPABILITY_NAMED_IAM --tags Key=project,Value=incident-commander-gpu \
    --parameters ParameterKey=SubnetId,ParameterValue="$SUB" ParameterKey=SecurityGroupId,ParameterValue="$SG" >/dev/null
  aws_ cloudformation wait stack-create-complete --stack-name "$STACK"
  echo "created $(iid). Next: infra/record.sh start 'YYYY-MM-DD HH:MM'  (UTC stop time)"
  ;;
start)
  STOP=${2:-}
  [ -n "$STOP" ] && { date -u -j -f '%Y-%m-%d %H:%M' "$STOP" +%s >/dev/null 2>&1 || date -u -d "$STOP" +%s >/dev/null 2>&1 \
    || { echo "stop time must look like '2026-10-23 06:00' (UTC)"; exit 1; }; }
  running
  say "Copying the recorder to the machine"
  git -C "$REPO" -c core.autocrlf=false archive HEAD testbed/app/src testbed/app/package.json testbed/app/package-lock.json \
    testbed/app/Dockerfile infra/record | gzip -9 | base64 | tr -d '\n' > "$TMP/rec.b64"
  split -b 20000 "$TMP/rec.b64" "$TMP/c_"
  n=0; for c in "$TMP"/c_*; do n=$((n+1)); { [ $n = 1 ] && echo 'rm -f ~/rec.b64'; echo "printf %s '$(cat "$c")' >> ~/rec.b64"; } > "$TMP/s.sh"; onhost 60 "$TMP/s.sh" >/dev/null; done
  say "Starting the recording"
  host 900 'set -e
mkdir -p ~/pulse-record && cd ~/pulse-record && base64 -d ~/rec.b64 | tar xzf - && mv -f infra/record/* . && sed -i "s/\r$//" compress_last_hour.sh check_last_hour.py docker-compose.yml && chmod +x compress_last_hour.sh && mkdir -p recordings
B="'"$BUCKET"'"
(crontab -l 2>/dev/null | grep -v -e compress_last_hour -e check_last_hour -e "s3 sync" || true; echo "5 * * * * $HOME/pulse-record/compress_last_hour.sh"; echo "35 * * * * nice -n 10 python3 $HOME/pulse-record/check_last_hour.py"
 [ -n "$B" ] && echo "15 * * * * /snap/bin/aws s3 sync $HOME/pulse-record/recordings s3://$B/ --exclude \"*\" --include \"*.gz\" --include \"*.check.txt\" --region eu-north-1 --only-show-errors") | crontab -
[ -n "$B" ] && echo "uploads each finished hour to s3://$B/ at :15"
STOP="'"$STOP"'"
if [ -n "$STOP" ]; then M=$(( ( $(date -u -d "$STOP" +%s) - $(date -u +%s) ) / 60 )); sudo shutdown -h +$M "recording window complete"; echo "machine stops itself at $STOP UTC (in $M min)"
else sudo shutdown -c; echo "no stop time: the machine records until infra/record.sh stop / pause"; fi
docker compose up -d --build 2>&1 | tail -2; sleep 20; docker compose logs collector --since 1m | grep -m1 -E "connected|error" || true'
  echo "Recording. Check any time: infra/record.sh status"
  ;;
status)
  ID=$(iid); { [ -z "$ID" ] || [ "$ID" = None ]; } && { echo "no recorder machine"; exit 0; }
  S=$(state "$ID"); echo "machine $ID: $S"; [ "$S" = running ] || exit 0
  host 120 'cd ~/pulse-record 2>/dev/null || { echo "recorder not set up yet: infra/record.sh start"; exit 0; }
docker compose ps --format "{{.Service}} {{.State}} {{.Status}}"; echo
ls -l --time-style=+%m-%d_%H:%M recordings | awk "NR>1 {printf \"%s  %7.1f MB  last write %s\n\", \$7, \$5/1e6, \$6}" | tail -8
echo "files: $(ls recordings | wc -l)   disk: $(df -h / | awk "NR==2{print \$4}") free"
docker compose logs collector --since 10m | grep -c "source disconnected" | xargs echo "disconnects in the last 10 min:"
U=$(sed -n "s/^USEC=//p" /run/systemd/shutdown/scheduled 2>/dev/null); [ -n "$U" ] && date -u -d @$((U/1000000)) "+scheduled stop: %F %H:%M UTC"
ls recordings/*.check.txt >/dev/null 2>&1 && { echo "worst gap in any finished hour: $(cat recordings/*.check.txt | sed -n "s/.*max_gap_s=//p" | sort -n | tail -1) s (rule: 60 s)"; }'
  ;;
run)   # run a shell snippet on the recorder as ubuntu (diagnostics): infra/record.sh run 'crontab -l'
  ID=$(iid); host 300 "${2:?usage: record.sh run 'commands'}"
  ;;
stop)
  running; host 120 'cd ~/pulse-record && docker compose down && echo "recorder stopped; files kept in ~/pulse-record/recordings"'
  ;;
pause)
  ID=$(iid); aws_ ec2 stop-instances --instance-ids "$ID" >/dev/null; echo "stopping $ID (disk and recordings kept)"
  ;;
down)
  read -r -p "Delete the recorder AND its recordings? type yes: " A; [ "$A" = yes ] || exit 1
  aws_ cloudformation delete-stack --stack-name "$STACK"; echo "deleting $STACK"
  ;;
*) sed -n 2,14p "$0"; exit 1 ;;
esac
