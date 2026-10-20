#!/bin/bash
# Start or stop everything Rosie runs, in a safe order (owner 2026-10-19: "a shutdown all button ... think about this as everyday use").
#   power.sh start | stop | status
# Lives in ~/rosie-ops, outside both code slots (like the watchdog). Used by Rosie's "Shut down all" button, the desktop launcher
# and the setup script. Things that aren't installed are skipped, so it works on any setup. ~/rosie-ops/power.conf can add more:
#   CONTAINERS="rosie-graph agent-zero"      USER_UNITS="assistant-dictation assistant-overlay my-other-service"
set -u
OPS="$(cd "$(dirname "$0")" && pwd)"
FLAG="$OPS/stopped"                                   # "stopped on purpose": the watchdog leaves Rosie alone; she removes it when she starts
CONTAINERS="rosie-graph agent-zero"                   # her memory graph, then Agent Zero (her brain)
USER_UNITS="assistant-dictation assistant-overlay"    # push-to-talk and the talking overlay
ROSIE_UNIT="assistant.service"                        # Rosie herself (a system service)
PORT="${ROSIE_PORT:-8765}"
[ -f "$OPS/power.conf" ] && . "$OPS/power.conf"

have_container() { command -v docker >/dev/null && docker inspect "$1" >/dev/null 2>&1; }
have_user_unit() { systemctl --user cat "$1" >/dev/null 2>&1; }
say() { echo "$1"; }

stop_all() {
  date -Is > "$FLAG"
  for c in $(echo "$CONTAINERS" | tr ' ' '\n' | tac); do          # Agent Zero first: it may be using her tools
    have_container "$c" && { docker stop -t 20 "$c" >/dev/null 2>&1 && say "stopped $c" || say "could not stop $c"; }
  done
  for u in $USER_UNITS; do
    have_user_unit "$u" && { systemctl --user stop "$u" && say "stopped $u" || say "could not stop $u"; }
  done
  if command -v curl >/dev/null; then                           # unload the AI models: frees the graphics card and memory
    for m in $(curl -s -m 5 http://127.0.0.1:11434/api/ps | python3 -c 'import json,sys
try: print("\n".join(x["name"] for x in json.load(sys.stdin).get("models", [])))
except Exception: pass' 2>/dev/null); do
      curl -s -m 30 http://127.0.0.1:11434/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" >/dev/null && say "unloaded $m"
    done
  fi
  if systemctl cat "$ROSIE_UNIT" >/dev/null 2>&1; then        # Rosie last
    systemctl stop "$ROSIE_UNIT" && say "stopped Rosie" || say "could not stop Rosie (try: sudo systemctl stop $ROSIE_UNIT)"
  fi
}

start_all() {
  rm -f "$FLAG"
  for c in $CONTAINERS; do
    have_container "$c" && { docker start "$c" >/dev/null 2>&1 && say "started $c" || say "could not start $c"; }
  done
  for u in $USER_UNITS; do
    have_user_unit "$u" && { systemctl --user start "$u" && say "started $u" || say "could not start $u"; }
  done
  if systemctl cat "$ROSIE_UNIT" >/dev/null 2>&1 && ! systemctl is-active --quiet "$ROSIE_UNIT"; then
    systemctl start "$ROSIE_UNIT" && say "started Rosie" || say "could not start Rosie (try: sudo systemctl start $ROSIE_UNIT)"
  fi
  for _ in $(seq 1 45); do                                      # ready when she answers
    curl -s -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && { say "Rosie is ready"; return 0; }
    sleep 2
  done
  say "Rosie did not answer within 90 seconds"; return 1
}

status_all() {
  for c in $CONTAINERS; do
    have_container "$c" && echo "$c $(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)"
  done
  for u in $USER_UNITS; do
    have_user_unit "$u" && echo "$u $(systemctl --user is-active "$u")"
  done
  systemctl cat "$ROSIE_UNIT" >/dev/null 2>&1 && echo "rosie $(systemctl is-active "$ROSIE_UNIT")"
  [ -f "$FLAG" ] && echo "stopped-on-purpose $(cat "$FLAG")"
  true
}

case "${1:-status}" in
  start) start_all ;;
  stop) stop_all ;;
  status) status_all ;;
  *) echo "usage: $0 start | stop | status" >&2; exit 2 ;;
esac
