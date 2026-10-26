#!/bin/bash
# board #813 owner-shutdown-everyday-use
set -u
OPS="$(cd "$(dirname "$0")" && pwd)"
FLAG="$OPS/stopped"  # board #813 why-stopped-flag-watchdog
CONTAINERS="rosie-graph agent-zero"
USER_UNITS="assistant-dictation assistant-overlay"
ROSIE_UNIT="assistant.service"
PORT="${ROSIE_PORT:-8765}"
[ -f "$OPS/power.conf" ] && . "$OPS/power.conf"

have_container() { command -v docker >/dev/null && docker inspect "$1" >/dev/null 2>&1; }
have_user_unit() { systemctl --user cat "$1" >/dev/null 2>&1; }
say() { echo "$1"; }

stop_all() {
  date -Is > "$FLAG"
  for c in $(echo "$CONTAINERS" | tr ' ' '\n' | tac); do  # board #813 why-agent-zero-first
    have_container "$c" && { docker stop -t 20 "$c" >/dev/null 2>&1 && say "stopped $c" || say "could not stop $c"; }
  done
  for u in $USER_UNITS; do
    have_user_unit "$u" && { systemctl --user stop "$u" && say "stopped $u" || say "could not stop $u"; }
  done
  if command -v curl >/dev/null; then  # board #813 why-free-gpu-memory
    for m in $(curl -s -m 5 http://127.0.0.1:11434/api/ps | python3 -c 'import json,sys
try: print("\n".join(x["name"] for x in json.load(sys.stdin).get("models", [])))
except Exception: pass' 2>/dev/null); do
      curl -s -m 30 http://127.0.0.1:11434/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" >/dev/null && say "unloaded $m"
    done
  fi
  if systemctl cat "$ROSIE_UNIT" >/dev/null 2>&1; then
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
  for _ in $(seq 1 45); do
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
