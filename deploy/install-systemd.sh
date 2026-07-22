#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-/opt/upbit-auto-trader}"
SERVICE_NAME="upbit-auto-trader.service"
ENV_FILE="/etc/upbit-auto-trader.env"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo deploy/install-systemd.sh ${APP_DIR}" >&2
  exit 1
fi

if ! id upbitbot >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin upbitbot
fi

mkdir -p "$APP_DIR"
chown -R upbitbot:upbitbot "$APP_DIR"
chmod +x "$APP_DIR/deploy/run-auto.sh"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$APP_DIR/deploy/upbit-auto-trader.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE. Edit it before starting the service."
fi

cp "$APP_DIR/deploy/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo "Installed $SERVICE_NAME."
echo "Edit $ENV_FILE, then run: sudo systemctl start $SERVICE_NAME"
