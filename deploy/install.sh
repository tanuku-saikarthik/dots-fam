#!/usr/bin/env bash
# Install Dots Fam as an always-on service on an Ubuntu/Debian server (made for Oracle Cloud's
# Always Free ARM VM, works on any small VPS). Safe to re-run: it updates what's there and
# never overwrites your .env.
#
#   git clone https://github.com/tanuku-saikarthik/dots-fam && cd dots-fam
#   ./deploy/install.sh                          # private HTTPS over Tailscale (default)
#   MODE=cloudflare CF_TUNNEL_TOKEN=... ./deploy/install.sh
#   MODE=caddy DOMAIN=dots.example.com ./deploy/install.sh
#   MODE=none ./deploy/install.sh                # you bring your own HTTPS
#
# Optional: VOICE=1 (local Whisper/Kokoro, wants 4+ GB RAM), SKIP_IMAGES=1 (skip Docker images).
set -euo pipefail

MODE="${MODE:-tailscale}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO/.env"
PORT="${PORT:-8787}"
SERVICE=dotsfam
RUN_USER="$(id -un)"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# set_env KEY VALUE [force]: write KEY=VALUE to .env, keeping an existing value unless forced.
set_env() {
  local key="$1" value="$2" force="${3:-}"
  if [ -z "$force" ] && grep -q "^${key}=." "$ENV_FILE" 2>/dev/null; then return; fi
  { grep -v "^${key}=" "$ENV_FILE" 2>/dev/null || true; echo "${key}=${value}"; } >"$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

get_env() { grep "^$1=" "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2-; }

render_unit() {
  cat <<UNIT
[Unit]
Description=Dots Fam
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=${RUN_USER}
SupplementaryGroups=docker
WorkingDirectory=${REPO}
ExecStart=${REPO}/.venv/bin/dotsfam
Restart=always
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full

[Install]
WantedBy=multi-user.target
UNIT
}

install_packages() {
  say "System packages"
  sudo apt-get update -qq
  sudo apt-get install -y -qq git curl ca-certificates build-essential >/dev/null
  sudo apt-get install -y -qq gh >/dev/null 2>&1 || echo "(gh not in apt here: pull requests will give you a compare link instead)"
  if ! have docker; then
    say "Docker"
    curl -fsSL https://get.docker.com | sudo sh
  fi
  sudo usermod -aG docker "$RUN_USER"
  sudo systemctl enable --now docker >/dev/null 2>&1 || true
}

install_backend() {
  say "Python environment"
  have uv || curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  cd "$REPO"
  [ -d .venv ] || uv venv --python 3.12 .venv
  local extras="computer,slack"
  [ "${VOICE:-}" = 1 ] && extras="computer,slack,voice"
  uv pip install --python .venv/bin/python -e "backend[${extras}]"
}

install_frontend() {
  say "Web app"
  local major=0
  have node && major="$(node -p 'process.versions.node.split(".")[0]')"
  if [ "$major" -lt 20 ]; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y -qq nodejs >/dev/null
  fi
  cd "$REPO/frontend"
  if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
  npm run build
}

build_images() {
  [ "${SKIP_IMAGES:-}" = 1 ] && return
  say "Docker images (a few minutes the first time)"
  cd "$REPO"
  sudo docker build -f computer/Dockerfile -t dotsfam-computer:latest .
  sudo docker build -t dotsfam-build sandbox/
}

write_env() {
  say "Settings"
  [ -f "$ENV_FILE" ] || { umask 077; cp "$REPO/.env.example" "$ENV_FILE"; }
  chmod 600 "$ENV_FILE"
  set_env HOST 127.0.0.1 force   # only the HTTPS front door below reaches the app
  set_env PORT "$PORT" force
  [ -n "$(get_env OWNER_TOKEN)" ] || { NEW_TOKEN=1; set_env OWNER_TOKEN "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"; }
  set_env DATA_DIR "$HOME/dotsfam-data"
  set_env COMPUTER_DRIVER docker
  if [ "${VOICE:-}" = 1 ]; then set_env VOICE_STACK local; else set_env VOICE_STACK off force; fi
  mkdir -p "$HOME/dotsfam-data"
}

install_service() {
  say "Service"
  render_unit | sudo tee "/etc/systemd/system/${SERVICE}.service" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE" >/dev/null 2>&1
  sudo systemctl restart "$SERVICE"
}

front_door() {
  case "$MODE" in
    tailscale)
      say "Tailscale (private HTTPS, only your own devices can reach it)"
      have tailscale || curl -fsSL https://tailscale.com/install.sh | sudo sh
      if ! sudo tailscale status >/dev/null 2>&1; then
        echo "Open the link below in a browser and sign in:"
        sudo tailscale up
      fi
      sudo tailscale serve --bg "$PORT" >/dev/null
      local host
      host="$(sudo tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
      set_env PUBLIC_URL "https://$host" force
      URL="https://$host"
      NOTE="In the Tailscale admin page (DNS), turn on HTTPS certificates. Install the Tailscale app on your phone and sign in to the same account. GitHub/Linear webhooks can't reach a private address: run 'sudo tailscale funnel --bg $PORT' if you need them."
      ;;
    cloudflare)
      [ -n "${CF_TUNNEL_TOKEN:-}" ] || die "MODE=cloudflare needs CF_TUNNEL_TOKEN (Zero Trust, Networks, Tunnels, create one, copy the token)."
      [ -n "${PUBLIC_URL:-}" ] || die "MODE=cloudflare needs PUBLIC_URL, the hostname you mapped to http://localhost:$PORT in the tunnel."
      say "Cloudflare Tunnel"
      if ! have cloudflared; then
        local arch; arch="$(dpkg --print-architecture)"
        curl -fsSL -o /tmp/cloudflared.deb "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
        sudo dpkg -i /tmp/cloudflared.deb
      fi
      sudo cloudflared service install "$CF_TUNNEL_TOKEN" || true
      set_env PUBLIC_URL "$PUBLIC_URL" force
      URL="$PUBLIC_URL"
      NOTE="Anyone on the internet can reach this address, so keep OWNER_TOKEN secret. Consider Cloudflare Access in front of it."
      ;;
    caddy)
      [ -n "${DOMAIN:-}" ] || die "MODE=caddy needs DOMAIN (a name that already points at this server)."
      say "Caddy (automatic HTTPS for $DOMAIN)"
      have caddy || sudo apt-get install -y -qq caddy >/dev/null
      printf '%s {\n\treverse_proxy 127.0.0.1:%s\n}\n' "$DOMAIN" "$PORT" | sudo tee /etc/caddy/Caddyfile >/dev/null
      # Oracle's Ubuntu images ship with a firewall rule that rejects everything else.
      for p in 80 443; do
        sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 1 -p tcp --dport "$p" -j ACCEPT
      done
      have netfilter-persistent && sudo netfilter-persistent save >/dev/null 2>&1 || true
      sudo systemctl restart caddy
      set_env PUBLIC_URL "https://$DOMAIN" force
      URL="https://$DOMAIN"
      NOTE="Also open ports 80 and 443 in your cloud firewall (Oracle: VCN, Security List, Ingress Rules). Anyone can reach this address, so keep OWNER_TOKEN secret."
      ;;
    none)
      URL="http://127.0.0.1:$PORT"
      NOTE="Put your own HTTPS in front of 127.0.0.1:$PORT. Phone calls and the microphone need https://."
      ;;
    *) die "MODE must be tailscale, cloudflare, caddy or none." ;;
  esac
  sudo systemctl restart "$SERVICE"
}

main() {
  [ "$(id -u)" -ne 0 ] || die "Run this as a normal user with sudo, not as root."
  have sudo || die "sudo is required."
  NEW_TOKEN=""; URL=""; NOTE=""
  install_packages
  install_backend
  install_frontend
  build_images
  write_env
  install_service
  front_door
  say "Done"
  echo "Dots Fam:    $URL"
  [ -n "$NEW_TOKEN" ] && echo "Owner token: $(get_env OWNER_TOKEN)   (also in $ENV_FILE; open the site and paste it once)"
  echo "Add your model key: edit $ENV_FILE (ANTHROPIC_API_KEY or OPENAI_API_KEY), then: sudo systemctl restart $SERVICE"
  echo "Logs:        journalctl -u $SERVICE -f"
  echo "Update later: ./deploy/update.sh"
  [ -n "$NOTE" ] && printf '\nNote: %s\n' "$NOTE"
  true
}

# Sourcing this file (for tests) defines the functions without installing anything.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
