from __future__ import annotations

import json
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any

from .config import Settings
from .trader import OrderPlan, Trader
from .upbit_client import UpbitApiError, UpbitResponse


@dataclass(frozen=True)
class AutoConfig:
    quote: str = "KRW"
    interval_seconds: int = 60
    cash_usage_percent: Decimal = Decimal("100")
    min_change_rate: Decimal = Decimal("0.005")
    min_24h_volume: Decimal = Decimal("1000000000")
    stop_loss_rate: Decimal = Decimal("-0.02")
    take_profit_rate: Decimal = Decimal("0.03")
    rotation_margin_rate: Decimal = Decimal("0.01")
    fee_buffer_rate: Decimal = Decimal("0.001")
    include_warnings: bool = False
    live: bool = False
    yes: bool = False
    allow_full_balance: bool = False
    once: bool = False
    stop_file: Path = Path(".upbit_bot_stop")
    log_file: Path = Path("upbit_auto_trader.jsonl")


@dataclass(frozen=True)
class Candidate:
    market: str
    score: Decimal
    change_rate: Decimal
    volume_24h: Decimal
    trade_price: Decimal


@dataclass(frozen=True)
class Position:
    market: str
    currency: str
    balance: Decimal
    avg_buy_price: Decimal
    current_price: Decimal
    value_krw: Decimal
    momentum_score: Decimal
    pnl_rate: Decimal | None


class AutoTrader:
    def __init__(self, settings: Settings, trader: Trader | None = None) -> None:
        self.settings = settings
        self.trader = trader or Trader(settings)
        self._running = True

    def run(self, config: AutoConfig) -> None:
        self._validate_config(config)
        self._clear_stop_file(config.stop_file)
        self._install_signal_handlers()
        self._log(config, "started", {"live": config.live, "once": config.once})

        while self._running:
            if config.stop_file.exists():
                self._log(config, "stopped", {"reason": "stop_file"})
                return

            try:
                summary = self.run_once(config)
                self._log(config, "cycle", summary)
            except (RuntimeError, ValueError, UpbitApiError) as exc:
                self._log(config, "error", {"error": str(exc)})

            if config.once:
                return
            self._sleep(config.interval_seconds, config.stop_file)

    def run_once(self, config: AutoConfig) -> dict[str, Any]:
        markets = [item["market"] for item in self.trader.markets(config.quote, config.include_warnings).data]
        tickers = self._load_tickers(markets)
        ticker_by_market = {item["market"]: item for item in tickers}
        candidate = self.select_candidate(tickers, config)

        balances = self.trader.balances().data
        positions = self.positions(balances, ticker_by_market, config.quote)
        cash = self.cash_balance(balances, config.quote)

        actions: list[dict[str, Any]] = []
        held_markets = {position.market for position in positions}

        for position in positions:
            sell_reason = self.sell_reason(position, candidate, config)
            if sell_reason:
                result = self.trader.market_sell(
                    position.market,
                    str(position.balance),
                    live=config.live,
                    yes=config.yes,
                )
                actions.append(self._action("sell", position.market, sell_reason, result))

        if not positions and candidate is not None and cash >= self.settings.min_order_krw:
            buy_amount = self.buy_amount(cash, config)
            if buy_amount >= self.settings.min_order_krw:
                result = self.trader.market_buy(
                    candidate.market,
                    str(buy_amount),
                    live=config.live,
                    yes=config.yes,
                )
                actions.append(self._action("buy", candidate.market, "top_candidate", result))
        elif positions and candidate is not None and candidate.market in held_markets:
            actions.append({"type": "hold", "market": candidate.market, "reason": "already_holding_top_candidate"})

        return {
            "candidate": self._candidate_dict(candidate),
            "cash": str(cash),
            "positions": [self._position_dict(position) for position in positions],
            "actions": actions,
        }

    def select_candidate(self, tickers: list[dict[str, Any]], config: AutoConfig) -> Candidate | None:
        candidates: list[Candidate] = []
        for item in tickers:
            change_rate = self._decimal(item.get("signed_change_rate", "0"))
            volume_24h = self._decimal(item.get("acc_trade_price_24h", "0"))
            trade_price = self._decimal(item.get("trade_price", "0"))
            if change_rate < config.min_change_rate:
                continue
            if volume_24h < config.min_24h_volume:
                continue
            if trade_price <= 0:
                continue
            score = change_rate * volume_24h
            candidates.append(
                Candidate(
                    market=str(item["market"]),
                    score=score,
                    change_rate=change_rate,
                    volume_24h=volume_24h,
                    trade_price=trade_price,
                )
            )
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate.score)

    def positions(
        self,
        balances: list[dict[str, Any]],
        ticker_by_market: dict[str, dict[str, Any]],
        quote: str,
    ) -> list[Position]:
        positions: list[Position] = []
        quote = quote.upper()
        for item in balances:
            currency = str(item.get("currency", "")).upper()
            if currency == quote:
                continue
            balance = self._decimal(item.get("balance", "0"))
            if balance <= 0:
                continue
            market = f"{quote}-{currency}"
            ticker = ticker_by_market.get(market)
            if ticker is None:
                continue
            avg_buy_price = self._decimal(item.get("avg_buy_price", "0"))
            current_price = self._decimal(ticker.get("trade_price", "0"))
            change_rate = self._decimal(ticker.get("signed_change_rate", "0"))
            volume_24h = self._decimal(ticker.get("acc_trade_price_24h", "0"))
            value_krw = balance * current_price
            momentum_score = max(change_rate, Decimal("0")) * volume_24h
            pnl_rate = None
            if avg_buy_price > 0:
                pnl_rate = (current_price / avg_buy_price) - Decimal("1")
            positions.append(
                Position(
                    market=market,
                    currency=currency,
                    balance=balance,
                    avg_buy_price=avg_buy_price,
                    current_price=current_price,
                    value_krw=value_krw,
                    momentum_score=momentum_score,
                    pnl_rate=pnl_rate,
                )
            )
        return positions

    def sell_reason(
        self,
        position: Position,
        candidate: Candidate | None,
        config: AutoConfig,
    ) -> str | None:
        if position.value_krw < self.settings.min_order_krw:
            return None
        if position.pnl_rate is not None and position.pnl_rate <= config.stop_loss_rate:
            return "stop_loss"
        if position.pnl_rate is not None and position.pnl_rate >= config.take_profit_rate:
            return "take_profit"
        if candidate is None:
            return None
        if candidate.market == position.market:
            return None
        if candidate.score > position.momentum_score * (Decimal("1") + config.rotation_margin_rate):
            return "rotate_to_stronger_candidate"
        return None

    def buy_amount(self, cash: Decimal, config: AutoConfig) -> Decimal:
        usable = cash * (config.cash_usage_percent / Decimal("100"))
        if config.fee_buffer_rate > 0:
            usable = usable * (Decimal("1") - config.fee_buffer_rate)
        if self.settings.max_order_krw > 0:
            usable = min(usable, self.settings.max_order_krw)
        return usable.quantize(Decimal("1"), rounding=ROUND_DOWN)

    def cash_balance(self, balances: list[dict[str, Any]], quote: str) -> Decimal:
        quote = quote.upper()
        for item in balances:
            if str(item.get("currency", "")).upper() == quote:
                return self._decimal(item.get("balance", "0"))
        return Decimal("0")

    def _load_tickers(self, markets: list[str]) -> list[dict[str, Any]]:
        tickers: list[dict[str, Any]] = []
        for start in range(0, len(markets), 80):
            chunk = markets[start : start + 80]
            if not chunk:
                continue
            response = self.trader.tickers(chunk)
            tickers.extend(response.data)
            time.sleep(0.12)
        return tickers

    def _validate_config(self, config: AutoConfig) -> None:
        if config.live:
            if not config.yes:
                raise RuntimeError("Live auto trading requires --yes.")
            if not self.settings.live_trading:
                raise RuntimeError("Set UPBIT_LIVE_TRADING=true before live auto trading.")
            self.settings.require_keys()
        if config.cash_usage_percent <= 0 or config.cash_usage_percent > 100:
            raise ValueError("--cash-usage-percent must be > 0 and <= 100")
        if config.cash_usage_percent >= 99:
            if not config.allow_full_balance or not self.settings.allow_full_balance_autotrade:
                raise RuntimeError(
                    "99%+ balance auto trading requires --allow-full-balance and AUTO_ALLOW_FULL_BALANCE=true."
                )
        if config.interval_seconds < 5:
            raise ValueError("--interval-seconds must be at least 5")

    def _sleep(self, seconds: int, stop_file: Path) -> None:
        end = time.time() + seconds
        while self._running and time.time() < end:
            if stop_file.exists():
                return
            time.sleep(min(1.0, end - time.time()))

    def _install_signal_handlers(self) -> None:
        def handle_stop(signum: int, frame: Any) -> None:
            self._running = False

        signal.signal(signal.SIGINT, handle_stop)
        signal.signal(signal.SIGTERM, handle_stop)

    @staticmethod
    def _clear_stop_file(stop_file: Path) -> None:
        if stop_file.exists():
            stop_file.unlink()

    def _log(self, config: AutoConfig, event: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }
        with config.log_file.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _action(order_type: str, market: str, reason: str, result: UpbitResponse | OrderPlan) -> dict[str, Any]:
        action = {"type": order_type, "market": market, "reason": reason}
        if isinstance(result, OrderPlan):
            action["dry_run"] = True
            action["order"] = result.payload
        else:
            action["dry_run"] = False
            action["status"] = result.status
            action["data"] = result.data
        return action

    @staticmethod
    def _candidate_dict(candidate: Candidate | None) -> dict[str, str] | None:
        if candidate is None:
            return None
        return {
            "market": candidate.market,
            "score": str(candidate.score),
            "change_rate": str(candidate.change_rate),
            "volume_24h": str(candidate.volume_24h),
            "trade_price": str(candidate.trade_price),
        }

    @staticmethod
    def _position_dict(position: Position) -> dict[str, str | None]:
        return {
            "market": position.market,
            "currency": position.currency,
            "balance": str(position.balance),
            "avg_buy_price": str(position.avg_buy_price),
            "current_price": str(position.current_price),
            "value_krw": str(position.value_krw),
            "momentum_score": str(position.momentum_score),
            "pnl_rate": str(position.pnl_rate) if position.pnl_rate is not None else None,
        }

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid decimal value: {value!r}") from exc
