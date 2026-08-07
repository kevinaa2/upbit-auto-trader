#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/upbit-auto-trader}"
cd "$APP_DIR"

DEFAULT_ARGS="run-auto --live --yes --allow-full-balance --cash-usage-percent 100 --interval-seconds 28800 --position-check-seconds 10 --use-openai-info --info-article-limit 200 --min-info-score-for-buy 0 --candidate-news-markets 10 --candidate-news-articles-per-market 10 --stop-loss-rate -0.06 --min-profit-exit-rate 0.05 --trailing-start-rate 0.05 --rotation-min-pnl-rate 0.05"
ARGS="${AUTO_TRADER_ARGS:-$DEFAULT_ARGS}"

exec python3 -m upbit_bot $ARGS
