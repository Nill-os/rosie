#!/bin/bash
# board #818 why-stable-entry-point
if [ -x "$HOME/rosie-ops/launch.sh" ]; then exec "$HOME/rosie-ops/launch.sh"; fi
cd "$(dirname "$0")/app" && exec ../.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8765
