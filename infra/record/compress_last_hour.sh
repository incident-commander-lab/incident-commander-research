#!/bin/bash
# cron :05 - gzip the previous (finished) hour's recording (~10x smaller to fetch); the original stays
# for the replay tools. Its CPU burst also keeps the host above the idle-stop alarm's 3% threshold.
cd ~/pulse-record/recordings || exit 0
f="wikipedia-$(date -u -d '1 hour ago' +%Y%m%d%H).ndjson"
[ -f "$f" ] && [ ! -f "$f.gz" ] && nice -n 10 gzip -9 -k "$f"
