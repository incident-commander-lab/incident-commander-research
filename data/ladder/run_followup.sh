#!/bin/bash
# After the main ladder: retry qwen3:0.6b (its download failed), then the lenient-format variant for
# every model (only strict failures are re-asked; strict passes are reused unchanged).
cd "$(dirname "$0")/../.."
while pgrep -f "run_ladder.sh" >/dev/null; do sleep 30; done
data/ladder/run_ladder.sh qwen3:0.6b
for m in qwen3:0.6b qwen3:1.7b qwen3:4b-q4_K_M qwen3:8b qwen3:14b; do
  ollama show "$m" >/dev/null 2>&1 || continue
  IC_LADDER_LENIENT=1 python3 -m ic.bench.ladder run "$m"
done
echo "FOLLOWUP_DONE $(date '+%F %T')"
