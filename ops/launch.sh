#!/bin/bash
# board #812 why-launcher-outside-slots
OPS="$(cd "$(dirname "$0")" && pwd)"
dir_for() { [ "$1" = "B" ] && echo "${ROSIE_SLOT_B:-$HOME/assistant-b}" || echo "${ROSIE_SLOT_A:-$HOME/assistant}"; }
ACTIVE=$(tr -d ' \n' < "$OPS/active" 2>/dev/null); ACTIVE=${ACTIVE:-A}
D=$(dir_for "$ACTIVE")
if [ ! -f "$D/app/server.py" ]; then
  OTHER=A; [ "$ACTIVE" = "A" ] && OTHER=B
  echo "$(date -Is) slot $ACTIVE is unusable, starting slot $OTHER" >> "$OPS/events.log"
  ACTIVE=$OTHER; D=$(dir_for "$ACTIVE")
fi
export ROSIE_SLOT="$ACTIVE"
cd "$D/app" && exec "${ROSIE_VENV:-$(dir_for A)/.venv}/bin/uvicorn" server:app --host 127.0.0.1 --port "${ROSIE_PORT:-8765}" --timeout-graceful-shutdown 3
