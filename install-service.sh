#!/bin/bash
# Run once with: sudo ./install-service.sh   (installs auto-start + Tailscale)
set -e
[ "$EUID" -eq 0 ] || { echo "Run with sudo: sudo ./install-service.sh"; exit 1; }
U="${SUDO_USER:-$USER}"; D="$(cd "$(dirname "$0")" && pwd)"
cat > /etc/systemd/system/assistant.service <<UNIT
[Unit]
Description=Personal assistant web server
After=network-online.target ollama.service
Wants=ollama.service

[Service]
User=$U
ExecStart=$D/start.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now assistant
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
echo "Done. Next: sudo tailscale up   then   sudo tailscale serve --bg 8765"
