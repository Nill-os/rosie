#!/bin/bash
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
OPS="$HOME/rosie-ops"
mkdir -p "$OPS"
cp "$SRC/launch.sh" "$SRC/slots.py" "$SRC/watchdog.py" "$SRC/autopilot.py" "$SRC/power.sh" "$SRC/open.sh" "$OPS/"
chmod +x "$OPS/launch.sh" "$OPS/slots.py" "$OPS/watchdog.py" "$OPS/autopilot.py" "$OPS/power.sh" "$OPS/open.sh"
[ -f "$OPS/active" ] || echo A > "$OPS/active"
python3 "$OPS/slots.py" ensure-b
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/rosie-watchdog.service" <<UNIT
[Unit]
Description=Rosie rollback watchdog (outside both code slots)
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 %h/rosie-ops/watchdog.py
UNIT
cat > "$HOME/.config/systemd/user/rosie-watchdog.timer" <<UNIT
[Unit]
Description=Check the new Rosie version every minute after a switch
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
UNIT
cat > "$HOME/.config/systemd/user/rosie-audit.service" <<UNIT
[Unit]
Description=Rosie autopilot audit
[Service]
Type=oneshot
TimeoutStartSec=7200
ExecStart=/usr/bin/python3 %h/rosie-ops/autopilot.py audit
UNIT
cat > "$HOME/.config/systemd/user/rosie-audit.timer" <<UNIT
[Unit]
Description=Rosie autopilot audit check
[Timer]
OnBootSec=10min
OnUnitActiveSec=20min
[Install]
WantedBy=timers.target
UNIT
cat > "$HOME/.config/systemd/user/rosie-midnight.service" <<UNIT
[Unit]
Description=Rosie autopilot midnight switch
[Service]
Type=oneshot
TimeoutStartSec=2400
ExecStart=/usr/bin/python3 %h/rosie-ops/autopilot.py midnight
UNIT
cat > "$HOME/.config/systemd/user/rosie-midnight.timer" <<UNIT
[Unit]
Description=Rosie autopilot midnight
[Timer]
OnCalendar=*-*-* 00:00:00
[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now rosie-watchdog.timer
systemctl --user enable --now rosie-audit.timer rosie-midnight.timer
echo "installed. active slot: $(cat "$OPS/active")"
