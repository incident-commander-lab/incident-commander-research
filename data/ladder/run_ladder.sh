#!/bin/bash
# Paper 2 model ladder driver: one model at a time, resumable (re-run to continue).
cd "$(dirname "$0")/../.."
pgrep -x ollama >/dev/null || { nohup ollama serve >> ~/.ollama-ladder.log 2>&1 & sleep 4; }
for m in ${@:-qwen3:8b qwen3:0.6b qwen3:1.7b qwen3:4b-q4_K_M qwen3:14b}; do
  for t in 1 2 3; do ollama show "$m" >/dev/null 2>&1 && break; ollama pull "$m" >/dev/null 2>&1; done
  ollama show "$m" >/dev/null 2>&1 || { echo "SKIP $m: download failed"; continue; }
  python3 -m ic.bench.ladder run "$m"
done
echo "LADDER_DONE $(date '+%F %T')"
