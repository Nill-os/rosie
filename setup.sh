#!/bin/bash
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
MODEL="${ROSIE_MODEL:-qwen3.5:9b}"
A0_PORT=50080
step() { printf '\n\033[1;36m== %s\033[0m\n' "$1"; }
note() { printf '   %s\n' "$1"; }

[ "$(id -u)" -ne 0 ] || { echo "Run this as yourself, not with sudo (it asks for your password when it needs it)."; exit 1; }
[ "$D" = "$HOME/assistant" ] || { echo "Rosie must live in ~/assistant. Run: git clone https://github.com/Nill-os/rosie ~/assistant"; exit 1; }
command -v apt-get >/dev/null || { echo "This setup script is for Ubuntu or Debian (apt)."; exit 1; }

step "1/11 System packages"
PKGS="python3-venv python3-dev python3-gi gir1.2-gtk-3.0 git curl tmux xdotool xclip libsndfile1 ffmpeg gnupg cups-client"
command -v docker >/dev/null || PKGS="$PKGS docker.io"
sudo apt-get update -qq && sudo apt-get install -y -qq $PKGS
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  note "You were added to the docker group. Log out and back in once, then run ./setup.sh again."
  exit 0
fi
BROWSER=""
for b in google-chrome-stable google-chrome chromium chromium-browser brave-browser; do command -v "$b" >/dev/null && { BROWSER="$b"; break; }; done
[ -n "$BROWSER" ] || note "No Chrome or Chromium found: install one (Rosie's window and the newspaper's PDF use it)."

step "2/11 Python environment"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt

step "3/11 Ollama and the AI model ($MODEL)"
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0:11434"\nEnvironment="OLLAMA_FLASH_ATTENTION=1"\nEnvironment="OLLAMA_KV_CACHE_TYPE=q8_0"\nEnvironment="LLAMA_ARG_CACHE_RAM=2048"\n' \
  | sudo tee /etc/systemd/system/ollama.service.d/rosie.conf >/dev/null
sudo systemctl daemon-reload && sudo systemctl restart ollama && sleep 3
note "Ollama listens on all interfaces so the Agent Zero container can reach it; block port 11434 in your firewall if this PC is on a shared network."
ollama pull "$MODEL" && ollama pull nomic-embed-text

step "4/11 Secrets (they stay on this PC)"
mkdir -p secrets data && chmod 700 secrets
[ -s secrets/session_key ] || head -c 32 /dev/urandom | base64 > secrets/session_key
[ -s secrets/access_code ] || printf '%06d\n' $(( $(od -An -N4 -tu4 /dev/urandom) % 1000000 )) > secrets/access_code
[ -s secrets/graphiti_falkordb_password ] || head -c 24 /dev/urandom | base64 | tr -d '/+=' > secrets/graphiti_falkordb_password
chmod 600 secrets/*

step "5/11 Your settings"
NAME=""
[ -t 0 ] && read -r -p "   What should Rosie call you? " NAME
.venv/bin/python - "$MODEL" "$NAME" <<'PY'
import sys
sys.path.insert(0, "app")
import config
cfg = config.load()
cfg["chat_model"] = sys.argv[1]
if sys.argv[2].strip():
    cfg["owner_name"] = sys.argv[2].strip()[:40]
cfg.setdefault("brain", "agent_zero")
config.save(cfg)
print("   saved")
PY

step "6/11 Rosie's memory graph (FalkorDB in Docker)"
if ! docker inspect rosie-graph >/dev/null 2>&1; then
  docker run -d --name rosie-graph --restart unless-stopped -p 127.0.0.1:6380:6379 -v rosie-graph-data:/var/lib/falkordb/data \
    -v "$D/secrets/graphiti_falkordb_password:/run/secrets/falkordb_password:ro" --entrypoint /bin/sh falkordb/falkordb-server:v4.22.2 \
    -c 'REDIS_ARGS="--requirepass $(cat /run/secrets/falkordb_password) --appendonly yes" exec /var/lib/falkordb/bin/run.sh' >/dev/null
fi
note "running"

step "7/11 Agent Zero (Rosie's brain, in Docker)"
mkdir -p "$HOME/agent-zero/usr/extensions/python/message_loop_start"
cp "$D/ops/agent-zero/step_cap.py" "$HOME/agent-zero/usr/extensions/python/message_loop_start/_20_step_cap.py"
if ! docker inspect agent-zero >/dev/null 2>&1; then
  docker run -d --name agent-zero --restart unless-stopped -p 127.0.0.1:$A0_PORT:80 --add-host host.docker.internal:host-gateway \
    -v "$HOME/agent-zero/usr:/a0/usr" agent0ai/agent-zero:latest >/dev/null
fi
for _ in $(seq 1 60); do [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$A0_PORT/)" = 200 ] && break; sleep 3; done
.venv/bin/python ops/agent-zero/configure.py "$MODEL"

step "8/11 Rosie's service, updates and helpers"
sudo ./install-service.sh
./ops/install.sh
./dictation-service.sh install || note "Push-to-talk needs a desktop session (X11); you can set it up later with ./dictation-service.sh install"

step "9/11 Desktop icon and app menu"
mkdir -p "$HOME/.local/share/icons" "$HOME/.local/share/applications"
cp static/icon.svg "$HOME/.local/share/icons/rosie.svg"
[ -n "$BROWSER" ] && ! grep -q '^BROWSER=' "$HOME/rosie-ops/power.conf" 2>/dev/null && echo "BROWSER=\"$(command -v "$BROWSER")\"" >> "$HOME/rosie-ops/power.conf"
for target in "$HOME/.local/share/applications/rosie.desktop" "$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")/rosie.desktop"; do
  mkdir -p "$(dirname "$target")"
  cat > "$target" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Rosie
Comment=Start everything (Rosie, Agent Zero, the AI models) and open Rosie
Exec=$HOME/rosie-ops/open.sh
Icon=$HOME/.local/share/icons/rosie.svg
Terminal=false
Categories=Utility;
StartupWMClass=assistant
Actions=agentzero;shutdown;

[Desktop Action agentzero]
Name=Open Agent Zero
Exec=$HOME/rosie-ops/open.sh agentzero

[Desktop Action shutdown]
Name=Shut down all
Exec=$HOME/rosie-ops/open.sh stop
EOF
  chmod +x "$target"
  command -v gio >/dev/null && gio set "$target" metadata::trusted true 2>/dev/null || true
done

step "10/11 Context window (how much the model can hold at once)"
.venv/bin/python -c 'import sys; sys.path.insert(0, "app"); import ctxsize; ctxsize.tune()' || note "Tune it later: Settings > Context window > Auto-tune"

step "11/11 Start"
"$HOME/rosie-ops/power.sh" start
cat <<EOF

Rosie is ready. Open her from the desktop icon (or the app menu: Rosie).
  Login code:  $(cat secrets/access_code)        (also in ~/assistant/secrets/access_code)
  Agent Zero:  right-click the icon > Open Agent Zero, or http://127.0.0.1:$A0_PORT
Optional next steps (README.md): Google Calendar and Gmail (./login.sh, ./login-gmail.sh main), the phone app over Tailscale,
the newspaper's place, feeds and printer (Settings > Newspaper).
EOF
