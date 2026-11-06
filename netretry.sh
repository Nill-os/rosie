net_wait() {
  local limit="${ROSIE_NET_WAIT:-120}" step="${ROSIE_NET_STEP:-5}" waited=0
  while ! getent hosts "${1:-github.com}" >/dev/null 2>&1; do
    [ "$waited" -ge "$limit" ] && return 1
    sleep "$step"
    waited=$((waited + step))
  done
}

net_retry() {
  local tries="${ROSIE_NET_TRIES:-3}" pause="${ROSIE_NET_PAUSE:-60}" n=1
  while true; do
    "$@" && return 0
    [ "$n" -ge "$tries" ] && return 1
    sleep "$pause"
    n=$((n + 1))
    net_wait || true
  done
}
