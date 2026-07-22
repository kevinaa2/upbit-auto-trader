# Upbit Live Trading Bot

Python standard-library based Upbit trading CLI.

This project can place real Upbit orders when live trading is explicitly enabled.
Keep API keys out of source control and start with small orders.

## Safety Defaults

- Real orders require both `UPBIT_LIVE_TRADING=true` and the CLI flag `--live`.
- Order commands also require `--yes`.
- `MAX_ORDER_KRW` limits market-buy order size.
- `AUTO_ALLOW_FULL_BALANCE=true` is required before the auto trader may use 99% or more of available KRW.
- The program never requests withdrawal permissions.

## Setup

PowerShell:

```powershell
$env:UPBIT_ACCESS_KEY="your_access_key"
$env:UPBIT_SECRET_KEY="your_secret_key"
$env:UPBIT_LIVE_TRADING="true"
$env:MAX_ORDER_KRW="10000"
$env:AUTO_ALLOW_FULL_BALANCE="false"
$env:AI_NEWS_FEEDS=""
$env:OPENAI_API_KEY=""
$env:OPENAI_MODEL="gpt-5"
$env:ALERTS_ENABLED="true"
$env:TELEGRAM_BOT_TOKEN=""
$env:TELEGRAM_CHAT_ID=""
```

Upbit Open API key permissions:

- Required for trading: order permission.
- Required for balances: asset/account read permission.
- Do not enable withdrawal permissions for this bot.
- Register the public IP address of the machine running the bot.

## Commands

Check current price:

```powershell
python -m upbit_bot ticker --market KRW-BTC
```

Check balances:

```powershell
python -m upbit_bot balances
```

Dry-run market buy:

```powershell
python -m upbit_bot buy --market KRW-BTC --krw 6000
```

Real market buy:

```powershell
python -m upbit_bot buy --market KRW-BTC --krw 6000 --live --yes
```

Dry-run market sell:

```powershell
python -m upbit_bot sell --market KRW-BTC --volume 0.00005
```

Real market sell:

```powershell
python -m upbit_bot sell --market KRW-BTC --volume 0.00005 --live --yes
```

Limit buy:

```powershell
python -m upbit_bot limit-buy --market KRW-BTC --volume 0.00005 --price 100000000 --live --yes
```

Limit sell:

```powershell
python -m upbit_bot limit-sell --market KRW-BTC --volume 0.00005 --price 120000000 --live --yes
```

Run autonomous dry-run loop:

```powershell
python -m upbit_bot run-auto --once --allow-full-balance
```

Run autonomous live loop using up to 100% of available KRW, after fee buffer and `MAX_ORDER_KRW`:

```powershell
$env:UPBIT_LIVE_TRADING="true"
$env:AUTO_ALLOW_FULL_BALANCE="true"
$env:MAX_ORDER_KRW="0"
python -m upbit_bot run-auto --live --yes --allow-full-balance --cash-usage-percent 100
```

Run autonomous loop with OpenAI news analysis:

```powershell
$env:OPENAI_API_KEY="your_openai_api_key"
python -m upbit_bot run-auto --use-openai-info --cash-usage-percent 50
```

Run without external news scoring:

```powershell
python -m upbit_bot run-auto --no-info
```

Send a test alert:

```powershell
python -m upbit_bot test-alert --message "alert test"
```

24/7 server operation:

- Use `deploy/upbit-auto-trader.service` for systemd.
- Store production secrets in `/etc/upbit-auto-trader.env`.
- See `docs/OPERATIONS.md` for setup, restart, logs, and alert testing.

Stop an autonomous loop from another terminal:

```powershell
python -m upbit_bot stop-auto
```

You can also stop the loop with `Ctrl+C`.

Sync local changes to GitHub:

```powershell
.\scripts\sync.ps1 "Update trading logic"
```

The sync script runs tests, commits changed files, and pushes to the connected GitHub repository.

Auto trader defaults:

- Scans all KRW markets.
- Excludes warning markets unless `--include-warnings` is passed.
- Buys the strongest candidate by 24h volume-adjusted positive momentum.
- Collects crypto news from RSS feeds and adjusts candidate scores with external information.
- Can optionally call the OpenAI Responses API when `--use-openai-info` and `OPENAI_API_KEY` are set.
- Sells on `--stop-loss-rate`, `--take-profit-rate`, or rotation to a stronger candidate.
- Sells when strong negative external information is detected for the held market.
- Blocks new buys when global crypto news risk is too negative.
- Writes JSONL logs to `upbit_auto_trader.jsonl`.
- Requires `AUTO_ALLOW_FULL_BALANCE=true` plus `--allow-full-balance` when `--cash-usage-percent` is 99 or higher.

Information scoring:

- Default RSS sources are Google News searches for crypto, Bitcoin, Ethereum, Korean crypto terms, and Upbit-related notices.
- Override sources with comma-separated RSS URLs in `AI_NEWS_FEEDS`.
- Keyword analysis is always available and does not require an OpenAI key.
- OpenAI analysis is optional and only scores the collected articles; the strategy engine still makes the final buy/sell decision.
- `--info-weight` controls how strongly information changes market momentum scores.
- `--info-sell-threshold` controls when negative information triggers a sell or blocks a candidate.
- `--global-risk-block-threshold` blocks new buys when broad market news risk is too negative.

Alerts:

- Telegram alerts use `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- Generic JSON webhook alerts use `ALERT_WEBHOOK_URL`.
- The bot alerts on start, stop, errors, buy/sell actions, and periodic heartbeats.
- `--alert-heartbeat-cycles 0` disables heartbeat alerts.

## Notes

- Market buy uses Upbit `ord_type=price`, where `price` is the total KRW amount.
- Market sell uses Upbit `ord_type=market`, where `volume` is the coin amount.
- All order identifiers are generated locally and are unique per command.
