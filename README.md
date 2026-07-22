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

Stop an autonomous loop from another terminal:

```powershell
python -m upbit_bot stop-auto
```

You can also stop the loop with `Ctrl+C`.

Auto trader defaults:

- Scans all KRW markets.
- Excludes warning markets unless `--include-warnings` is passed.
- Buys the strongest candidate by 24h volume-adjusted positive momentum.
- Sells on `--stop-loss-rate`, `--take-profit-rate`, or rotation to a stronger candidate.
- Writes JSONL logs to `upbit_auto_trader.jsonl`.
- Requires `AUTO_ALLOW_FULL_BALANCE=true` plus `--allow-full-balance` when `--cash-usage-percent` is 99 or higher.

## Notes

- Market buy uses Upbit `ord_type=price`, where `price` is the total KRW amount.
- Market sell uses Upbit `ord_type=market`, where `volume` is the coin amount.
- All order identifiers are generated locally and are unique per command.
