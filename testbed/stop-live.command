#!/bin/bash
# Stop the live service and the agent's model. Data (database, answer key, releases) is kept.
cd "$(dirname "$0")"
printf '\n\033[1m%s\033[0m\n' "Stopping the live service..."
docker compose down
pkill -x ollama 2>/dev/null && echo "Ollama stopped."
printf '\n\033[1m%s\033[0m\n' "Stopped. Docker Desktop is left running (your other containers use it)."
