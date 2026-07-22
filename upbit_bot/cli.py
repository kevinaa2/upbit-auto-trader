from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from .auto_trader import AutoConfig, AutoTrader
from .config import Settings
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
    auto.add_argument("--min-24h-volume", default="1000000000")
    auto.add_argument("--stop-loss-rate", default="-0.02")
    auto.add_argument("--take-profit-rate", default="0.03")
    auto.add_argument("--rotation-margin-rate", default="0.01")
    auto.add_argument("--fee-buffer-rate", default="0.001")
    auto.add_argument("--no-info", action="store_true", help="Disable external news/RSS information scoring.")
    auto.add_argument("--use-openai-info", action="store_true", help="Use OpenAI API to analyze collected news.")
    auto.add_argument("--info-weight", default="0.25")
    auto.add_argument("--info-sell-threshold", default="-0.70")
    auto.add_argument("--global-risk-block-threshold", default="-0.80")
    auto.add_argument("--info-article-limit", type=int, default=40)
    auto.add_argument("--include-warnings", action="store_true")
    auto.add_argument("--allow-full-balance", action="store_true")
    auto.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    auto.add_argument("--stop-file", default=".upbit_bot_stop")
    auto.add_argument("--log-file", default="upbit_auto_trader.jsonl")

    stop = subparsers.add_parser("stop-auto", help="Stop a running autonomous loop.")
    stop.add_argument("--stop-file", default=".upbit_bot_stop")

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
            min_24h_volume=Decimal(args.min_24h_volume),
            stop_loss_rate=Decimal(args.stop_loss_rate),
            take_profit_rate=Decimal(args.take_profit_rate),
            rotation_margin_rate=Decimal(args.rotation_margin_rate),
            fee_buffer_rate=Decimal(args.fee_buffer_rate),
            use_info=not args.no_info,
            use_openai_info=args.use_openai_info,
            info_weight=Decimal(args.info_weight),
            info_sell_threshold=Decimal(args.info_sell_threshold),
            global_risk_block_threshold=Decimal(args.global_risk_block_threshold),
            info_article_limit=args.info_article_limit,
            include_warnings=args.include_warnings,
            live=args.live,
            yes=args.yes,
            allow_full_balance=args.allow_full_balance,
            once=args.once,
            stop_file=Path(args.stop_file),
            log_file=Path(args.log_file),
        )
        AutoTrader(trader.settings, trader).run(config)
        return UpbitResponse({"message": "auto trader stopped", "log_file": str(config.log_file)}, 200, None)
    if args.command == "stop-auto":
        stop_file = Path(args.stop_file)
        stop_file.write_text("stop\n", encoding="utf-8")
        return UpbitResponse({"message": "stop signal written", "stop_file": str(stop_file)}, 200, None)
    raise ValueError(f"unknown command: {args.command}")


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
