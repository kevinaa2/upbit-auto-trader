from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from .auto_trader import AutoConfig, AutoTrader
from .config import Settings
from .notifier import Notification, Notifier
from .status_web import StatusWebConfig, run_status_server
from .trader import OrderPlan, Trader
from .upbit_client import UpbitApiError, UpbitResponse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="upbit-bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ticker = subparsers.add_parser("ticker", help="Get current ticker data.")
    ticker.add_argument("--market", required=True)

    subparsers.add_parser("balances", help="Get Upbit account balances.")

    buy = subparsers.add_parser("buy", help="Create a market buy order.")
    _add_order_flags(buy)
    buy.add_argument("--market", required=True)
    buy.add_argument("--krw", required=True, help="Total KRW amount to spend.")

    sell = subparsers.add_parser("sell", help="Create a market sell order.")
    _add_order_flags(sell)
    sell.add_argument("--market", required=True)
    sell.add_argument("--volume", required=True, help="Coin amount to sell.")

    limit_buy = subparsers.add_parser("limit-buy", help="Create a limit buy order.")
    _add_order_flags(limit_buy)
    limit_buy.add_argument("--market", required=True)
    limit_buy.add_argument("--volume", required=True)
    limit_buy.add_argument("--price", required=True)

    limit_sell = subparsers.add_parser("limit-sell", help="Create a limit sell order.")
    _add_order_flags(limit_sell)
    limit_sell.add_argument("--market", required=True)
    limit_sell.add_argument("--volume", required=True)
    limit_sell.add_argument("--price", required=True)

    auto = subparsers.add_parser("run-auto", help="Run autonomous KRW-market trading loop.")
    _add_order_flags(auto)
    auto.add_argument("--quote", default="KRW")
    auto.add_argument("--interval-seconds", type=int, default=60)
    auto.add_argument("--cash-usage-percent", default="100")
    auto.add_argument("--min-change-rate", default="0.005")
    auto.add_argument("--max-change-rate", default="0.12")
    auto.add_argument("--overheat-info-threshold", default="0.50")
    auto.add_argument("--min-24h-volume", default="1000000000")
    auto.add_argument("--stop-loss-rate", default="-0.02")
    auto.add_argument("--take-profit-rate", default="0")
    auto.add_argument("--trailing-start-rate", default="0.04")
    auto.add_argument("--trailing-stop-rate-1", default="0.03")
    auto.add_argument("--trailing-stop-rate-2", default="0.04")
    auto.add_argument("--trailing-stop-rate-3", default="0.06")
    auto.add_argument("--trailing-tier-2-rate", default="0.08")
    auto.add_argument("--trailing-tier-3-rate", default="0.15")
    auto.add_argument("--rotation-margin-rate", default="0.01")
    auto.add_argument("--fee-buffer-rate", default="0.001")
    auto.add_argument("--no-info", action="store_true", help="Disable external news/RSS information scoring.")
    auto.add_argument("--use-openai-info", action="store_true", help="Use OpenAI API to analyze collected news.")
    auto.add_argument("--info-weight", default="0.25")
    auto.add_argument("--info-sell-threshold", default="-0.70")
    auto.add_argument("--global-risk-block-threshold", default="-0.80")
    auto.add_argument("--info-article-limit", type=int, default=80)
    auto.add_argument("--min-info-articles-for-buy", type=int, default=1)
    auto.add_argument("--include-warnings", action="store_true")
    auto.add_argument("--allow-full-balance", action="store_true")
    auto.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    auto.add_argument("--alert-heartbeat-cycles", type=int, default=30)
    auto.add_argument("--stop-file", default=".upbit_bot_stop")
    auto.add_argument("--log-file", default="upbit_auto_trader.jsonl")
    auto.add_argument("--state-file", default=".upbit_auto_state.json")

    stop = subparsers.add_parser("stop-auto", help="Stop a running autonomous loop.")
    stop.add_argument("--stop-file", default=".upbit_bot_stop")

    alert = subparsers.add_parser("test-alert", help="Send a test alert.")
    alert.add_argument("--message", default="Alert test from upbit-auto-trader.")

    subparsers.add_parser("check-env", help="Show masked environment configuration diagnostics.")

    status_web = subparsers.add_parser("status-web", help="Run a read-only status web dashboard.")
    status_web.add_argument("--host", default="127.0.0.1")
    status_web.add_argument("--port", type=int, default=8080)
    status_web.add_argument("--log-file", default="upbit_auto_trader.jsonl")
    status_web.add_argument("--state-file", default=".upbit_auto_state.json")
    status_web.add_argument("--stop-file", default=".upbit_bot_stop")
    status_web.add_argument("--stale-after-seconds", type=int, default=900)
    status_web.add_argument("--recent-limit", type=int, default=25)

    return parser


def _add_order_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--live", action="store_true", help="Send a real order to Upbit.")
    parser.add_argument("--yes", action="store_true", help="Confirm real order execution.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    trader = Trader(settings)

    try:
        result = _dispatch(args, trader)
    except (RuntimeError, ValueError, UpbitApiError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(_format_result(result))
    return 0


def _dispatch(args: argparse.Namespace, trader: Trader) -> UpbitResponse | OrderPlan:
    if args.command == "ticker":
        return trader.ticker(args.market)
    if args.command == "balances":
        return trader.balances()
    if args.command == "check-env":
        return UpbitResponse(_env_diagnostics(trader.settings), 200, None)
    if args.command == "buy":
        return trader.market_buy(args.market, args.krw, args.live, args.yes)
    if args.command == "sell":
        return trader.market_sell(args.market, args.volume, args.live, args.yes)
    if args.command == "limit-buy":
        return trader.limit_buy(args.market, args.volume, args.price, args.live, args.yes)
    if args.command == "limit-sell":
        return trader.limit_sell(args.market, args.volume, args.price, args.live, args.yes)
    if args.command == "run-auto":
        config = AutoConfig(
            quote=args.quote,
            interval_seconds=args.interval_seconds,
            cash_usage_percent=Decimal(args.cash_usage_percent),
            min_change_rate=Decimal(args.min_change_rate),
            max_change_rate=Decimal(args.max_change_rate),
            overheat_info_threshold=Decimal(args.overheat_info_threshold),
            min_24h_volume=Decimal(args.min_24h_volume),
            stop_loss_rate=Decimal(args.stop_loss_rate),
            take_profit_rate=Decimal(args.take_profit_rate),
            trailing_start_rate=Decimal(args.trailing_start_rate),
            trailing_stop_rate_1=Decimal(args.trailing_stop_rate_1),
            trailing_stop_rate_2=Decimal(args.trailing_stop_rate_2),
            trailing_stop_rate_3=Decimal(args.trailing_stop_rate_3),
            trailing_tier_2_rate=Decimal(args.trailing_tier_2_rate),
            trailing_tier_3_rate=Decimal(args.trailing_tier_3_rate),
            rotation_margin_rate=Decimal(args.rotation_margin_rate),
            fee_buffer_rate=Decimal(args.fee_buffer_rate),
            use_info=not args.no_info,
            use_openai_info=args.use_openai_info,
            info_weight=Decimal(args.info_weight),
            info_sell_threshold=Decimal(args.info_sell_threshold),
            global_risk_block_threshold=Decimal(args.global_risk_block_threshold),
            info_article_limit=args.info_article_limit,
            min_info_articles_for_buy=args.min_info_articles_for_buy,
            include_warnings=args.include_warnings,
            live=args.live,
            yes=args.yes,
            allow_full_balance=args.allow_full_balance,
            once=args.once,
            alert_heartbeat_cycles=args.alert_heartbeat_cycles,
            stop_file=Path(args.stop_file),
            log_file=Path(args.log_file),
            state_file=Path(args.state_file),
        )
        AutoTrader(trader.settings, trader).run(config)
        return UpbitResponse({"message": "auto trader stopped", "log_file": str(config.log_file)}, 200, None)
    if args.command == "stop-auto":
        stop_file = Path(args.stop_file)
        stop_file.write_text("stop\n", encoding="utf-8")
        return UpbitResponse({"message": "stop signal written", "stop_file": str(stop_file)}, 200, None)
    if args.command == "test-alert":
        notifier = Notifier(trader.settings)
        if not notifier.enabled():
            raise RuntimeError(
                "No alert channel configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or ALERT_WEBHOOK_URL."
            )
        errors = notifier.send(
            Notification(
                event="test_alert",
                title="Alert test",
                level="info",
                details={"message": args.message},
            )
        )
        if errors:
            raise RuntimeError("; ".join(errors))
        return UpbitResponse({"message": "test alert sent"}, 200, None)
    if args.command == "status-web":
        run_status_server(
            StatusWebConfig(
                host=args.host,
                port=args.port,
                log_file=Path(args.log_file),
                state_file=Path(args.state_file),
                stop_file=Path(args.stop_file),
                stale_after_seconds=args.stale_after_seconds,
                recent_limit=args.recent_limit,
            )
        )
        return UpbitResponse({"message": "status web stopped"}, 200, None)
    raise ValueError(f"unknown command: {args.command}")


def _env_diagnostics(settings: Settings) -> dict[str, Any]:
    env_file = Path(".env")
    env_values = _read_env_file_values(env_file)
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_model = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()
    env_openai_key = env_values.get("OPENAI_API_KEY", "")
    runtime_matches_env = (
        bool(openai_key)
        and bool(env_openai_key)
        and openai_key == env_openai_key
    )
    return {
        "env_file": {
            "path": str(env_file),
            "exists": env_file.exists(),
            "has_openai_api_key": bool(env_openai_key),
            "openai_api_key": _mask_secret(env_openai_key),
        },
        "runtime": {
            "upbit_access_key_set": bool(settings.access_key),
            "upbit_secret_key_set": bool(settings.secret_key),
            "openai_api_key_set": bool(openai_key),
            "openai_api_key": _mask_secret(openai_key),
            "openai_model": openai_model,
        },
        "checks": {
            "runtime_openai_key_matches_env_file": runtime_matches_env,
            "possible_environment_override": bool(openai_key)
            and bool(env_openai_key)
            and openai_key != env_openai_key,
        },
    }


def _read_env_file_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if name:
            values[name] = value
    return values


def _mask_secret(value: str) -> dict[str, Any]:
    if not value:
        return {"set": False, "length": 0, "preview": ""}
    if len(value) <= 10:
        preview = value[0] + "***" + value[-1]
    else:
        preview = value[:7] + "..." + value[-4:]
    return {"set": True, "length": len(value), "preview": preview}


def _format_result(result: UpbitResponse | OrderPlan) -> str:
    if isinstance(result, OrderPlan):
        payload: dict[str, Any] = {
            "ok": True,
            "dry_run": True,
            "message": "No order was sent. Add --live --yes and set UPBIT_LIVE_TRADING=true to trade.",
            "order": result.payload,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    payload = {
        "ok": True,
        "dry_run": False,
        "status": result.status,
        "remaining_req": result.remaining_req,
        "data": result.data,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
