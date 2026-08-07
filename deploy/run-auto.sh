#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/upbit-auto-trader}"
cd "$APP_DIR"

DEFAULT_ARGS="run-auto --live --yes --allow-full-balance --cash-usage-percent 100 --interval-seconds 600 --use-openai-info --min-info-score-for-buy 0 --candidate-news-markets 5 --candidate-news-articles-per-market 5 --stop-loss-rate -0.05 --rotation-min-pnl-rate 0"
ARGS="${AUTO_TRADER_ARGS:-$DEFAULT_ARGS}"

exec python3 -m upbit_bot $ARGS
