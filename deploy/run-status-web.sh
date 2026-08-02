#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/upbit-auto-trader}"
cd "$APP_DIR"

DEFAULT_ARGS="status-web --host 127.0.0.1 --port 8080"
ARGS="${STATUS_WEB_ARGS:-$DEFAULT_ARGS}"

exec python3 -m upbit_bot $ARGS
