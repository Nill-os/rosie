#!/bin/bash
UNIT="$HOME/.config/systemd/user/assistant-dictation.service"
OVERLAY_UNIT="$HOME/.config/systemd/user/assistant-overlay.service"
case "$1" in
 install)
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<UNIT
[Unit]
Description=Assistant push-to-talk dictation
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Environment=PATH=$HOME/.local/bin:/usr/bin:/bin
# the LIVE code slot (~/assistant or ~/assistant-b, per ~/rosie-ops/active), not always slot A (audit 2026-10-23)
ExecStart=/bin/sh -c 'R=%h/assistant; [ "\$\$(cat %h/rosie-ops/active 2>/dev/null)" = B ] && R=%h/assistant-b; exec "\$\$R/.venv/bin/python" "\$\$R/app/dictation.py"'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
UNIT
  cat > "$OVERLAY_UNIT" <<UNIT
[Unit]
Description=Assistant octopus overlay and talk orb
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=/bin/sh -c 'R=%h/assistant; [ "\$\$(cat %h/rosie-ops/active 2>/dev/null)" = B ] && R=%h/assistant-b; exec /usr/bin/python3 "\$\$R/app/overlay.py"'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
UNIT
  systemctl --user daemon-reload && systemctl --user enable --now assistant-overlay assistant-dictation && echo "Installed and started. Dictation: hold your dictation key. AI: hold your AI key (default Right Alt) or press and hold the round octopus button." ;;
 remove)  systemctl --user disable --now assistant-dictation assistant-overlay 2>&1; rm -f "$UNIT" "$OVERLAY_UNIT"; systemctl --user daemon-reload; echo "Removed." ;;
 restart) systemctl --user restart assistant-overlay assistant-dictation && echo restarted ;;
 status)  systemctl --user status assistant-dictation assistant-overlay --no-pager 2>&1 | head -24 ;;
 *) echo "Usage: $0 install|remove|status|restart" ;;
esac
