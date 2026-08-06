from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any

from .config import Settings
from .intelligence import InfoSignal, IntelligenceEngine
from .notifier import Notification, Notifier
from .trader import OrderPlan, Trader
from .upbit_client import UpbitApiError, UpbitResponse


@dataclass(frozen=True)
class AutoConfig:
    quote: str = "KRW"
    interval_seconds: int = 60
    cash_usage_percent: Decimal = Decimal("100")
    min_change_rate: Decimal = Decimal("0.005")
    max_change_rate: Decimal = Decimal("0.12")
    overheat_info_threshold: Decimal = Decimal("0.50")
    min_24h_volume: Decimal = Decimal("1000000000")
    stop_loss_rate: Decimal = Decimal("-0.02")
    take_profit_rate: Decimal = Decimal("0")
    trailing_start_rate: Decimal = Decimal("0.04")
    trailing_stop_rate_1: Decimal = Decimal("0.03")
    trailing_stop_rate_2: Decimal = Decimal("0.04")
    trailing_stop_rate_3: Decimal = Decimal("0.06")
    trailing_tier_2_rate: Decimal = Decimal("0.08")
    trailing_tier_3_rate: Decimal = Decimal("0.15")
    rotation_margin_rate: Decimal = Decimal("0.01")
    fee_buffer_rate: Decimal = Decimal("0.001")
    use_info: bool = True
    use_openai_info: bool = False
    info_weight: Decimal = Decimal("0.25")
    info_sell_threshold: Decimal = Decimal("-0.70")
    global_risk_block_threshold: Decimal = Decimal("-0.80")
    info_article_limit: int = 80
    candidate_news_markets: int = 5
    candidate_news_articles_per_market: int = 5
    min_info_articles_for_buy: int = 1
    min_info_score_for_buy: Decimal = Decimal("0")
    include_warnings: bool = False
    live: bool = False
    yes: bool = False
    allow_full_balance: bool = False
    once: bool = False
    alert_heartbeat_cycles: int = 30
    stop_file: Path = Path(".upbit_bot_stop")
    log_file: Path = Path("upbit_auto_trader.jsonl")
    state_file: Path = Path(".upbit_auto_state.json")


@dataclass(frozen=True)
class Candidate:
    market: str
    score: Decimal
    market_score: Decimal
    info_score: Decimal
    change_rate: Decimal
    volume_24h: Decimal
    trade_price: Decimal
    reasons: list[str]


@dataclass(frozen=True)
class Position:
    market: str
    currency: str
    balance: Decimal
    avg_buy_price: Decimal
    current_price: Decimal
    value_krw: Decimal
    momentum_score: Decimal
    info_score: Decimal
    peak_price: Decimal
    trailing_stop_price: Decimal | None
    pnl_rate: Decimal | None


class AutoTrader:
    def __init__(
        self,
        settings: Settings,
        trader: Trader | None = None,
        intelligence: IntelligenceEngine | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.settings = settings
        self.trader = trader or Trader(settings)
        self.intelligence = intelligence
        self.notifier = notifier or Notifier(settings)
        self._running = True
        self._active_log_file = Path("upbit_auto_trader.jsonl")
        self._position_state: dict[str, dict[str, str]] = {}

    def run(self, config: AutoConfig) -> None:
        self._validate_config(config)
        self._active_log_file = config.log_file
        self._position_state = self._load_state(config.state_file)
        self._clear_stop_file(config.stop_file)
        self._install_signal_handlers()
        self._log(config, "started", {"live": config.live, "once": config.once})
        self._notify("started", "Auto trader started", "info", {"live": config.live, "once": config.once})
        cycle_count = 0

        while self._running:
            if config.stop_file.exists():
                self._log(config, "stopped", {"reason": "stop_file"})
                self._notify("stopped", "Auto trader stopped", "info", {"reason": "stop_file"})
                return

            try:
                summary = self.run_once(config)
                self._log(config, "cycle", summary)
                cycle_count += 1
                self._notify_cycle(config, cycle_count, summary)
            except (RuntimeError, ValueError, UpbitApiError) as exc:
                self._log(config, "error", {"error": str(exc)})
                self._notify("error", "Auto trader error", "error", {"error": str(exc)})

            if config.once:
                self._notify("stopped", "Auto trader finished one cycle", "info", {"reason": "once"})
                return
            self._sleep(config.interval_seconds, config.stop_file)
        self._log(config, "stopped", {"reason": "signal"})
        self._notify("stopped", "Auto trader stopped", "info", {"reason": "signal"})

    def run_once(self, config: AutoConfig) -> dict[str, Any]:
        market_items = self.trader.markets(config.quote, config.include_warnings).data
        markets = [item["market"] for item in market_items]
        tickers = self._load_tickers(markets)
        ticker_by_market = {item["market"]: item for item in tickers}
        info_signal = self._load_info_signal(market_items, config)
        preliminary_candidates = self.select_candidates(
            tickers,
            config,
            info_signal,
            limit=config.candidate_news_markets,
        )
        if preliminary_candidates:
            info_signal.merge(self._load_candidate_info_signal(market_items, preliminary_candidates, config))
        candidate = self.select_candidate(tickers, config, info_signal)

        balances = self.trader.balances().data
        positions = self.positions(balances, ticker_by_market, config.quote, info_signal)
        positions = self.apply_position_state(positions, config)
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

        if any(action.get("type") == "sell" and not action.get("dry_run") for action in actions):
            balances = self.trader.balances().data
            positions = self.positions(balances, ticker_by_market, config.quote, info_signal)
            positions = self.apply_position_state(positions, config)
            cash = self.cash_balance(balances, config.quote)
            held_markets = {position.market for position in positions}

        self._save_state(config.state_file)

        risk_off = info_signal.global_risk_score <= config.global_risk_block_threshold
        buy_block_reason = self.new_buy_block_reason(candidate, info_signal, config)
        if (
            not positions
            and candidate is not None
            and cash >= self.settings.min_order_krw
            and not risk_off
            and buy_block_reason is None
        ):
            buy_amount = self.buy_amount(cash, config)
            if buy_amount >= self.settings.min_order_krw:
                result = self.trader.market_buy(
                    candidate.market,
                    str(buy_amount),
                    live=config.live,
                    yes=config.yes,
                )
                actions.append(self._action("buy", candidate.market, "top_candidate", result))
        elif not positions and candidate is not None and buy_block_reason is not None:
            actions.append({"type": "skip_buy", "market": candidate.market, "reason": buy_block_reason})
        elif positions and candidate is not None and candidate.market in held_markets:
            actions.append({"type": "hold", "market": candidate.market, "reason": "already_holding_top_candidate"})

        return {
            "candidate": self._candidate_dict(candidate),
            "info": self._info_dict(info_signal),
            "cash": str(cash),
            "positions": [self._position_dict(position) for position in positions],
            "actions": actions,
        }

    def select_candidate(
        self,
        tickers: list[dict[str, Any]],
        config: AutoConfig,
        info_signal: InfoSignal | None = None,
    ) -> Candidate | None:
        candidates = self.select_candidates(tickers, config, info_signal, limit=1)
        if not candidates:
            return None
        return candidates[0]

    def select_candidates(
        self,
        tickers: list[dict[str, Any]],
        config: AutoConfig,
        info_signal: InfoSignal | None = None,
        limit: int = 5,
    ) -> list[Candidate]:
        info_signal = info_signal or InfoSignal()
        candidates: list[Candidate] = []
        if limit <= 0:
            return candidates
        for item in tickers:
            market = str(item["market"])
            change_rate = self._decimal(item.get("signed_change_rate", "0"))
            volume_24h = self._decimal(item.get("acc_trade_price_24h", "0"))
            trade_price = self._decimal(item.get("trade_price", "0"))
            info_score = info_signal.market_score(market)
            if change_rate < config.min_change_rate:
                continue
            if (
                config.max_change_rate > 0
                and change_rate > config.max_change_rate
                and info_score < config.overheat_info_threshold
            ):
                continue
            if volume_24h < config.min_24h_volume:
                continue
            if trade_price <= 0:
                continue
            if market in info_signal.blocked_markets:
                continue
            if info_score <= config.info_sell_threshold:
                continue
            market_score = change_rate * volume_24h
            info_multiplier = max(Decimal("0"), Decimal("1") + (info_score * config.info_weight))
            score = market_score * info_multiplier
            candidates.append(
                Candidate(
                    market=market,
                    score=score,
                    market_score=market_score,
                    info_score=info_score,
                    change_rate=change_rate,
                    volume_24h=volume_24h,
                    trade_price=trade_price,
                    reasons=info_signal.market_reasons.get(market, [])[:5],
                )
            )
        return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)[:limit]

    def positions(
        self,
        balances: list[dict[str, Any]],
        ticker_by_market: dict[str, dict[str, Any]],
        quote: str,
        info_signal: InfoSignal | None = None,
    ) -> list[Position]:
        info_signal = info_signal or InfoSignal()
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
            if value_krw < self.settings.min_order_krw:
                continue
            momentum_score = max(change_rate, Decimal("0")) * volume_24h
            info_score = info_signal.market_score(market)
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
                    info_score=info_score,
                    peak_price=current_price,
                    trailing_stop_price=None,
                    pnl_rate=pnl_rate,
                )
            )
        return positions

    def apply_position_state(self, positions: list[Position], config: AutoConfig) -> list[Position]:
        active_markets = {position.market for position in positions}
        for market in list(self._position_state):
            if market not in active_markets:
                del self._position_state[market]

        enriched: list[Position] = []
        for position in positions:
            state = self._position_state.get(position.market, {})
            stored_avg = self._decimal(state.get("avg_buy_price", "0"))
            stored_peak = self._decimal(state.get("peak_price", "0"))
            if stored_avg != position.avg_buy_price:
                stored_peak = Decimal("0")

            peak_price = max(stored_peak, position.current_price, position.avg_buy_price)
            trailing_stop_price = None
            if position.pnl_rate is not None and position.pnl_rate >= config.trailing_start_rate:
                stop_rate = self.trailing_stop_rate(position.pnl_rate, config)
                trailing_stop_price = peak_price * (Decimal("1") - stop_rate)

            self._position_state[position.market] = {
                "avg_buy_price": str(position.avg_buy_price),
                "peak_price": str(peak_price),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            enriched.append(
                Position(
                    market=position.market,
                    currency=position.currency,
                    balance=position.balance,
                    avg_buy_price=position.avg_buy_price,
                    current_price=position.current_price,
                    value_krw=position.value_krw,
                    momentum_score=position.momentum_score,
                    info_score=position.info_score,
                    peak_price=peak_price,
                    trailing_stop_price=trailing_stop_price,
                    pnl_rate=position.pnl_rate,
                )
            )
        return enriched

    def sell_reason(
        self,
        position: Position,
        candidate: Candidate | None,
        config: AutoConfig,
    ) -> str | None:
        if position.value_krw < self.settings.min_order_krw:
            return None
        if position.info_score <= config.info_sell_threshold:
            return "negative_external_info"
        if position.pnl_rate is not None and position.pnl_rate <= config.stop_loss_rate:
            return "stop_loss"
        if self.trailing_stop_hit(position, config):
            return "trailing_take_profit"
        if (
            config.take_profit_rate > 0
            and position.pnl_rate is not None
            and position.pnl_rate >= config.take_profit_rate
        ):
            return "take_profit"
        if candidate is None:
            return None
        if candidate.market == position.market:
            return None
        position_score = position.momentum_score * max(
            Decimal("0"),
            Decimal("1") + (position.info_score * config.info_weight),
        )
        if candidate.score > position_score * (Decimal("1") + config.rotation_margin_rate):
            return "rotate_to_stronger_candidate"
        return None

    def new_buy_block_reason(
        self,
        candidate: Candidate | None,
        info_signal: InfoSignal,
        config: AutoConfig,
    ) -> str | None:
        if not config.use_info:
            return None
        if info_signal.errors:
            return "information_errors"
        if info_signal.article_count < config.min_info_articles_for_buy:
            return "not_enough_information"
        if (
            candidate is not None
            and info_signal.market_scores.get(candidate.market, Decimal("0")) < config.min_info_score_for_buy
        ):
            return "candidate_info_below_minimum"
        return None

    def trailing_stop_hit(self, position: Position, config: AutoConfig) -> bool:
        if position.pnl_rate is None or position.pnl_rate < config.trailing_start_rate:
            return False
        if position.trailing_stop_price is None:
            return False
        return position.current_price <= position.trailing_stop_price

    def trailing_stop_rate(self, pnl_rate: Decimal, config: AutoConfig) -> Decimal:
        if pnl_rate >= config.trailing_tier_3_rate:
            return config.trailing_stop_rate_3
        if pnl_rate >= config.trailing_tier_2_rate:
            return config.trailing_stop_rate_2
        return config.trailing_stop_rate_1

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

    def _load_info_signal(self, market_items: list[dict[str, Any]], config: AutoConfig) -> InfoSignal:
        if not config.use_info:
            return InfoSignal(summary="External information disabled.")
        engine = self.intelligence or IntelligenceEngine(use_openai=config.use_openai_info)
        try:
            return engine.evaluate(market_items, limit=config.info_article_limit)
        except Exception as exc:
            return InfoSignal(errors=[f"information analysis failed: {exc}"])

    def _load_candidate_info_signal(
        self,
        market_items: list[dict[str, Any]],
        candidates: list[Candidate],
        config: AutoConfig,
    ) -> InfoSignal:
        if not config.use_info or config.candidate_news_markets <= 0:
            return InfoSignal()
        engine = self.intelligence or IntelligenceEngine(use_openai=config.use_openai_info)
        if not hasattr(engine, "evaluate_for_markets"):
            return InfoSignal()
        market_by_name = {str(item.get("market", "")).upper(): item for item in market_items}
        candidate_items = [
            market_by_name[candidate.market]
            for candidate in candidates[: config.candidate_news_markets]
            if candidate.market in market_by_name
        ]
        if not candidate_items:
            return InfoSignal()
        try:
            signal = engine.evaluate_for_markets(
                candidate_items,
                articles_per_market=config.candidate_news_articles_per_market,
            )
            signal.errors = []
            return signal
        except Exception as exc:
            return InfoSignal(summary=f"Candidate information analysis skipped: {exc}")

    def _validate_config(self, config: AutoConfig) -> None:
        if config.live:
            if not config.yes:
                raise RuntimeError("Live auto trading requires --yes.")
            if not self.settings.live_trading:
                raise RuntimeError("Set UPBIT_LIVE_TRADING=true before live auto trading.")
            self.settings.require_keys()
        if config.cash_usage_percent <= 0 or config.cash_usage_percent > 100:
            raise ValueError("--cash-usage-percent must be > 0 and <= 100")
        if config.max_change_rate < 0:
            raise ValueError("--max-change-rate must be 0 or greater")
        if config.overheat_info_threshold < -1 or config.overheat_info_threshold > 1:
            raise ValueError("--overheat-info-threshold must be between -1 and 1")
        if config.cash_usage_percent >= 99:
            if not config.allow_full_balance or not self.settings.allow_full_balance_autotrade:
                raise RuntimeError(
                    "99%+ balance auto trading requires --allow-full-balance and AUTO_ALLOW_FULL_BALANCE=true."
                )
        if config.interval_seconds < 5:
            raise ValueError("--interval-seconds must be at least 5")
        if config.info_article_limit < 1:
            raise ValueError("--info-article-limit must be at least 1")
        if config.candidate_news_markets < 0:
            raise ValueError("--candidate-news-markets must be 0 or greater")
        if config.candidate_news_articles_per_market < 0:
            raise ValueError("--candidate-news-articles-per-market must be 0 or greater")
        if config.min_info_articles_for_buy < 0:
            raise ValueError("--min-info-articles-for-buy must be 0 or greater")
        if config.min_info_score_for_buy < -1 or config.min_info_score_for_buy > 1:
            raise ValueError("--min-info-score-for-buy must be between -1 and 1")
        if config.alert_heartbeat_cycles < 0:
            raise ValueError("--alert-heartbeat-cycles must be 0 or greater")
        if config.trailing_start_rate < 0:
            raise ValueError("--trailing-start-rate must be 0 or greater")
        for name, value in {
            "--trailing-stop-rate-1": config.trailing_stop_rate_1,
            "--trailing-stop-rate-2": config.trailing_stop_rate_2,
            "--trailing-stop-rate-3": config.trailing_stop_rate_3,
        }.items():
            if value <= 0 or value >= 1:
                raise ValueError(f"{name} must be greater than 0 and less than 1")
        if config.trailing_tier_2_rate < config.trailing_start_rate:
            raise ValueError("--trailing-tier-2-rate must be >= --trailing-start-rate")
        if config.trailing_tier_3_rate < config.trailing_tier_2_rate:
            raise ValueError("--trailing-tier-3-rate must be >= --trailing-tier-2-rate")

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

    def _load_state(self, state_file: Path) -> dict[str, dict[str, str]]:
        if not state_file.exists():
            return {}
        try:
            with state_file.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        positions = data.get("positions", {})
        if not isinstance(positions, dict):
            return {}
        return {
            str(market): values
            for market, values in positions.items()
            if isinstance(values, dict)
        }

    def _save_state(self, state_file: Path) -> None:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "positions": self._position_state,
        }
        temp_file = state_file.with_suffix(state_file.suffix + ".tmp")
        with temp_file.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_file, state_file)

    def _notify_cycle(self, config: AutoConfig, cycle_count: int, summary: dict[str, Any]) -> None:
        actions = summary.get("actions", [])
        trade_actions = [action for action in actions if action.get("type") in {"buy", "sell"}]
        if trade_actions:
            self._notify(
                "trade_action",
                "Trade action executed" if config.live else "Trade action planned",
                "warning" if config.live else "info",
                {
                    "actions": trade_actions,
                    "candidate": summary.get("candidate"),
                    "cash": summary.get("cash"),
                },
            )
            return
        if config.alert_heartbeat_cycles and cycle_count % config.alert_heartbeat_cycles == 0:
            self._notify(
                "heartbeat",
                "Auto trader heartbeat",
                "info",
                {
                    "cycle": cycle_count,
                    "candidate": summary.get("candidate"),
                    "positions": summary.get("positions"),
                    "cash": summary.get("cash"),
                    "info": summary.get("info"),
                },
            )

    def _notify(self, event: str, title: str, level: str, details: dict[str, Any]) -> None:
        errors = self.notifier.send(
            Notification(event=event, title=title, level=level, details=details)
        )
        if errors:
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "alert_error",
                "alert_event": event,
                "errors": errors,
            }
            with self._active_log_file.open("a", encoding="utf-8") as file:
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
    def _candidate_dict(candidate: Candidate | None) -> dict[str, Any] | None:
        if candidate is None:
            return None
        return {
            "market": candidate.market,
            "score": str(candidate.score),
            "market_score": str(candidate.market_score),
            "info_score": str(candidate.info_score),
            "change_rate": str(candidate.change_rate),
            "volume_24h": str(candidate.volume_24h),
            "trade_price": str(candidate.trade_price),
            "reasons": candidate.reasons,
        }

    @staticmethod
    def _info_dict(signal: InfoSignal) -> dict[str, Any]:
        return {
            "article_count": signal.article_count,
            "global_risk_score": str(signal.global_risk_score),
            "blocked_markets": sorted(signal.blocked_markets),
            "summary": signal.summary,
            "errors": signal.errors,
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
            "info_score": str(position.info_score),
            "peak_price": str(position.peak_price),
            "trailing_stop_price": (
                str(position.trailing_stop_price) if position.trailing_stop_price is not None else None
            ),
            "pnl_rate": str(position.pnl_rate) if position.pnl_rate is not None else None,
        }

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid decimal value: {value!r}") from exc
