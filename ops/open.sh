#!/bin/bash
# Open Rosie (the desktop icon and the app menu): start whatever isn't running (power.sh start), then her window.
#   open.sh            Rosie's window
#   open.sh agentzero  Agent Zero's own window
#   open.sh stop       Shut down all (power.sh stop), with a notification when it's done
# ~/rosie-ops/power.conf can set BROWSER (a Chromium-type browser) and APP_PROFILE (its profile folder for the app window).
set -u
OPS="$(cd "$(dirname "$0")" && pwd)"
PORT="${ROSIE_PORT:-8765}"
BROWSER=""
APP_PROFILE="$HOME/.config/assistant-app"
A0_URL="http://127.0.0.1:50080"
[ -f "$OPS/power.conf" ] && . "$OPS/power.conf"
if [ -z "$BROWSER" ]; then
  for b in google-chrome-stable google-chrome chromium chromium-browser brave-browser; do command -v "$b" >/dev/null && { BROWSER="$b"; break; }; done
fi
ICON="$HOME/.local/share/icons/rosie.svg"

if [ "${1:-}" = "stop" ]; then
  notify-send -i "$ICON" "Rosie" "Shutting everything down…" 2>/dev/null
  "$OPS/power.sh" stop >/tmp/rosie-stop.log 2>&1
  notify-send -i "$ICON" "Rosie" "Everything is shut down. Open Rosie to start it again." 2>/dev/null
  exit 0
fi

if ! curl -s -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 || [ -f "$OPS/stopped" ]; then
  notify-send -i "$ICON" "Rosie" "Starting everything…" 2>/dev/null
  if ! "$OPS/power.sh" start >/tmp/rosie-start.log 2>&1; then
    notify-send -i "$ICON" "Rosie" "Rosie didn't start. Details: /tmp/rosie-start.log" 2>/dev/null
    exit 1
  fi
else
  "$OPS/power.sh" start >/dev/null 2>&1 &              # she's up: make sure her helpers and Agent Zero are too (quietly)
fi

if [ "${1:-}" = "agentzero" ]; then
  for _ in $(seq 1 30); do curl -s -m 2 -o /dev/null "$A0_URL/" && break; sleep 2; done
  exec "$BROWSER" --app="$A0_URL"
fi
exec "$BROWSER" --app="http://127.0.0.1:$PORT" --user-data-dir="$APP_PROFILE" --class=assistant
