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

Short run instructions are also available in `RUNNING.txt`.

PowerShell:

```powershell
$env:UPBIT_ACCESS_KEY="your_access_key"
$env:UPBIT_SECRET_KEY="your_secret_key"
$env:UPBIT_LIVE_TRADING="true"
$env:MAX_ORDER_KRW="10000"
$env:AUTO_ALLOW_FULL_BALANCE="false"
$env:AI_NEWS_FEEDS=""
$env:OPENAI_API_KEY=""
$env:OPENAI_MODEL="gpt-5-mini"
$env:ALERTS_ENABLED="true"
$env:TELEGRAM_BOT_TOKEN=""
$env:TELEGRAM_CHAT_ID=""
```

PyCharm local setup:

1. Open this project folder in PyCharm.
2. Copy `.env.local.example` to `.env`.
3. Fill only your local keys in `.env`.
4. Create a Python run configuration:
   - Script path: `run_bot.py`
   - Parameters: `ticker --market KRW-BTC`
   - Working directory: project root
5. For a dry-run auto cycle, use parameters:

```text
run-auto --once --cash-usage-percent 50
```

The app automatically loads `.env` from the project root. The `.env` file is ignored by Git.

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

Run the read-only status web dashboard:

```powershell
python -m upbit_bot status-web --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080` to see the last cycle, recent errors, current candidate, held positions, and stop status. The dashboard only reads `upbit_auto_trader.jsonl`, `.upbit_auto_state.json`, and `.upbit_bot_stop`; it does not expose buy or sell actions.

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
- Skips overheated candidates above `--max-change-rate 0.12` unless information score is at least `--overheat-info-threshold 0.50`.
- Collects crypto news from RSS feeds and adjusts candidate scores with external information.
- Can optionally call the OpenAI Responses API when `--use-openai-info` and `OPENAI_API_KEY` are set.
- Sells on `--stop-loss-rate`, trailing take-profit, optional `--take-profit-rate`, or rotation to a stronger candidate.
- Sells when strong negative external information is detected for the held market.
- Blocks new buys when global crypto news risk is too negative.
- Writes JSONL logs to `upbit_auto_trader.jsonl`.
- Stores position peak-price state in `.upbit_auto_state.json`.
- Provides a read-only status web dashboard with `status-web`.
- Requires `AUTO_ALLOW_FULL_BALANCE=true` plus `--allow-full-balance` when `--cash-usage-percent` is 99 or higher.

Trailing take-profit defaults:

- Fixed take-profit is disabled by default with `--take-profit-rate 0`.
- Trailing starts after `--trailing-start-rate 0.04`, meaning roughly +4% profit.
- From +4% to +8%, sell if price falls 3% from the observed peak.
- From +8% to +15%, sell if price falls 4% from the observed peak.
- Above +15%, sell if price falls 6% from the observed peak.
- The peak is persisted in `.upbit_auto_state.json`, so restarting the bot does not forget the latest observed high.

Example:

```powershell
python -m upbit_bot run-auto --trailing-start-rate 0.04 --trailing-stop-rate-1 0.03 --trailing-stop-rate-2 0.04 --trailing-stop-rate-3 0.06
```

Information scoring:

- Default RSS sources include CoinDesk, Cointelegraph, Cointelegraph Korea, Decrypt, and Google News searches.
- Google News searches include broad crypto news, hack/exploit/delisting risk, ETF/SEC news, major altcoin news, Korean crypto terms, Upbit notices, Bithumb notices, and Coinone notices.
- Korean source searches also include TokenPost, Blockmedia, Digital Asset, Decenter, CoinReaders, Coinness, and ZDNet Korea.
- Override all RSS sources with comma-separated RSS URLs in `AI_NEWS_FEEDS`.
- Append extra RSS sources with comma-separated RSS URLs in `AI_EXTRA_NEWS_FEEDS`.
- Override Google News search queries with `AI_NEWS_QUERIES`, separated by `|`.
- Append extra Google News search queries with `AI_EXTRA_NEWS_QUERIES`, separated by `|`.
- Keyword analysis is always available and does not require an OpenAI key.
- OpenAI analysis is optional and only scores the collected articles; the strategy engine still makes the final buy/sell decision.
- `--info-weight` controls how strongly information changes market momentum scores.
- `--info-sell-threshold` controls when negative information triggers a sell or blocks a candidate.
- `--global-risk-block-threshold` blocks new buys when broad market news risk is too negative.
- `--max-change-rate` avoids chasing sudden 24h price spikes; use `0` to disable it.
- `--overheat-info-threshold` allows an overheated candidate only when matched news/AI information is strongly positive.
- The strategy combines market score and information score. Market score comes from 24h volume-adjusted positive momentum, while information score comes from matched news and optional OpenAI analysis.

Example extra sources:

```env
AI_EXTRA_NEWS_FEEDS=https://example.com/feed.xml,https://example2.com/rss
AI_EXTRA_NEWS_QUERIES=site:example.com SOL partnership when:7d|site:example2.com XRP lawsuit when:7d
```

Alerts:

- Telegram alerts use `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- Generic JSON webhook alerts use `ALERT_WEBHOOK_URL`.
- The bot alerts on start, stop, errors, buy/sell actions, and periodic heartbeats.
- `--alert-heartbeat-cycles 0` disables heartbeat alerts.

## Notes

- Market buy uses Upbit `ord_type=price`, where `price` is the total KRW amount.
- Market sell uses Upbit `ord_type=market`, where `volume` is the coin amount.
- All order identifiers are generated locally and are unique per command.
