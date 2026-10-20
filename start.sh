#!/bin/bash
# Stable entry point for systemd. The real launcher lives OUTSIDE both code slots (~/rosie-ops), so a broken update can never break starting up.
if [ -x "$HOME/rosie-ops/launch.sh" ]; then exec "$HOME/rosie-ops/launch.sh"; fi
cd "$(dirname "$0")/app" && exec ../.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8765
