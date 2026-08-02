from __future__ import annotations

import os
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from upbit_bot.auto_trader import AutoConfig, AutoTrader, Position
from upbit_bot.config import Settings, load_dotenv
from upbit_bot.intelligence import InfoSignal, KeywordInfoAnalyzer, NewsCollector, NewsItem
from upbit_bot.notifier import Notification, Notifier
from upbit_bot.trader import OrderPlan, Trader
from upbit_bot.upbit_client import UpbitClient


class FakeClient:
    def __init__(self) -> None:
        self.orders: list[dict[str, str]] = []

    def create_order(self, payload: dict[str, str]):
        self.orders.append(payload)
        return "created"


class UpbitClientTests(unittest.TestCase):
    def test_jwt_contains_three_segments(self) -> None:
        client = UpbitClient("access", "secret")
        token = client._jwt("market=KRW-BTC&side=bid")
        self.assertEqual(len(token.split(".")), 3)

    def test_query_string_keeps_expected_form(self) -> None:
        self.assertEqual(
            UpbitClient._query_string({"market": "KRW-BTC", "side": "bid"}),
            "market=KRW-BTC&side=bid",
        )


class ConfigTests(unittest.TestCase):
    def test_load_dotenv_reads_values_without_overriding_existing_env(self) -> None:
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "UPBIT_ACCESS_KEY=from_file\n"
                "QUOTED_VALUE=\"hello world\"\n",
                encoding="utf-8",
            )
            original = os.environ.get("UPBIT_ACCESS_KEY")
            os.environ["UPBIT_ACCESS_KEY"] = "existing"
            try:
                self.assertTrue(load_dotenv(env_file))
                self.assertEqual(os.environ["UPBIT_ACCESS_KEY"], "existing")
                self.assertEqual(os.environ["QUOTED_VALUE"], "hello world")
            finally:
                if original is None:
                    os.environ.pop("UPBIT_ACCESS_KEY", None)
                else:
                    os.environ["UPBIT_ACCESS_KEY"] = original
                os.environ.pop("QUOTED_VALUE", None)


class TraderTests(unittest.TestCase):
    def settings(self, live: bool = False) -> Settings:
        return Settings(
            access_key="access",
            secret_key="secret",
            base_url="https://api.upbit.com",
            live_trading=live,
            max_order_krw=Decimal("10000"),
            min_order_krw=Decimal("5000"),
            allow_full_balance_autotrade=False,
            alerts_enabled=False,
            telegram_bot_token="",
            telegram_chat_id="",
            alert_webhook_url="",
        )

    def test_market_buy_dry_run(self) -> None:
        trader = Trader(self.settings(), client=FakeClient())  # type: ignore[arg-type]
        plan = trader.market_buy("krw-btc", "6000", live=False, yes=False)
        self.assertIsInstance(plan, OrderPlan)
        self.assertEqual(plan.payload["market"], "KRW-BTC")
        self.assertEqual(plan.payload["side"], "bid")
        self.assertEqual(plan.payload["ord_type"], "price")
        self.assertEqual(plan.payload["price"], "6000")

    def test_real_order_requires_live_env(self) -> None:
        trader = Trader(self.settings(live=False), client=FakeClient())  # type: ignore[arg-type]
        with self.assertRaises(RuntimeError):
            trader.market_buy("KRW-BTC", "6000", live=True, yes=True)

    def test_market_buy_respects_max_order_krw(self) -> None:
        trader = Trader(self.settings(), client=FakeClient())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            trader.market_buy("KRW-BTC", "10001", live=False, yes=False)

    def test_live_market_sell_sends_order(self) -> None:
        fake = FakeClient()
        trader = Trader(self.settings(live=True), client=fake)  # type: ignore[arg-type]
        result = trader.market_sell("KRW-BTC", "0.001", live=True, yes=True)
        self.assertEqual(result, "created")
        self.assertEqual(fake.orders[0]["side"], "ask")
        self.assertEqual(fake.orders[0]["ord_type"], "market")


class AutoTraderTests(unittest.TestCase):
    def settings(
        self,
        allow_full_balance: bool = False,
        max_order_krw: Decimal = Decimal("0"),
    ) -> Settings:
        return Settings(
            access_key="access",
            secret_key="secret",
            base_url="https://api.upbit.com",
            live_trading=True,
            max_order_krw=max_order_krw,
            min_order_krw=Decimal("5000"),
            allow_full_balance_autotrade=allow_full_balance,
            alerts_enabled=False,
            telegram_bot_token="",
            telegram_chat_id="",
            alert_webhook_url="",
        )

    def test_select_candidate_uses_positive_volume_adjusted_momentum(self) -> None:
        auto = AutoTrader(self.settings())
        config = AutoConfig(
            cash_usage_percent=Decimal("50"),
            min_change_rate=Decimal("0.01"),
            min_24h_volume=Decimal("1000"),
        )
        candidate = auto.select_candidate(
            [
                {
                    "market": "KRW-AAA",
                    "signed_change_rate": "0.02",
                    "acc_trade_price_24h": "1000",
                    "trade_price": "10",
                },
                {
                    "market": "KRW-BBB",
                    "signed_change_rate": "0.015",
                    "acc_trade_price_24h": "100000",
                    "trade_price": "10",
                },
            ],
            config,
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.market, "KRW-BBB")

    def test_select_candidate_skips_negative_information(self) -> None:
        auto = AutoTrader(self.settings())
        config = AutoConfig(
            cash_usage_percent=Decimal("50"),
            min_change_rate=Decimal("0.01"),
            min_24h_volume=Decimal("1000"),
        )
        signal = InfoSignal(
            market_scores={"KRW-AAA": Decimal("-0.9"), "KRW-BBB": Decimal("0")},
            blocked_markets={"KRW-AAA"},
        )
        candidate = auto.select_candidate(
            [
                {
                    "market": "KRW-AAA",
                    "signed_change_rate": "0.05",
                    "acc_trade_price_24h": "100000",
                    "trade_price": "10",
                },
                {
                    "market": "KRW-BBB",
                    "signed_change_rate": "0.015",
                    "acc_trade_price_24h": "100000",
                    "trade_price": "10",
                },
            ],
            config,
            signal,
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.market, "KRW-BBB")

    def test_full_balance_requires_double_unlock(self) -> None:
        auto = AutoTrader(self.settings(allow_full_balance=False))
        with self.assertRaises(RuntimeError):
            auto._validate_config(
                AutoConfig(
                    live=True,
                    yes=True,
                    cash_usage_percent=Decimal("100"),
                    allow_full_balance=True,
                )
            )

    def test_buy_amount_applies_fee_buffer_and_max_order(self) -> None:
        auto = AutoTrader(self.settings(allow_full_balance=True, max_order_krw=Decimal("10000")))
        amount = auto.buy_amount(
            Decimal("20000"),
            AutoConfig(
                cash_usage_percent=Decimal("100"),
                fee_buffer_rate=Decimal("0.001"),
                allow_full_balance=True,
            ),
        )
        self.assertEqual(amount, Decimal("10000"))

    def test_default_take_profit_does_not_sell_before_trailing_stop(self) -> None:
        auto = AutoTrader(self.settings())
        position = Position(
            market="KRW-BTC",
            currency="BTC",
            balance=Decimal("1"),
            avg_buy_price=Decimal("100"),
            current_price=Decimal("105"),
            value_krw=Decimal("105000"),
            momentum_score=Decimal("1000"),
            info_score=Decimal("0"),
            peak_price=Decimal("105"),
            trailing_stop_price=Decimal("101.85"),
            pnl_rate=Decimal("0.05"),
        )
        self.assertIsNone(auto.sell_reason(position, None, AutoConfig()))

    def test_trailing_take_profit_sells_after_pullback_from_peak(self) -> None:
        auto = AutoTrader(self.settings())
        position = Position(
            market="KRW-BTC",
            currency="BTC",
            balance=Decimal("1"),
            avg_buy_price=Decimal("100"),
            current_price=Decimal("113"),
            value_krw=Decimal("113000"),
            momentum_score=Decimal("1000"),
            info_score=Decimal("0"),
            peak_price=Decimal("120"),
            trailing_stop_price=Decimal("114"),
            pnl_rate=Decimal("0.13"),
        )
        self.assertEqual(
            auto.sell_reason(position, None, AutoConfig()),
            "trailing_take_profit",
        )

    def test_apply_position_state_keeps_highest_observed_price(self) -> None:
        auto = AutoTrader(self.settings())
        auto._position_state = {"KRW-BTC": {"avg_buy_price": "100", "peak_price": "120"}}
        position = Position(
            market="KRW-BTC",
            currency="BTC",
            balance=Decimal("1"),
            avg_buy_price=Decimal("100"),
            current_price=Decimal("113"),
            value_krw=Decimal("113000"),
            momentum_score=Decimal("1000"),
            info_score=Decimal("0"),
            peak_price=Decimal("113"),
            trailing_stop_price=None,
            pnl_rate=Decimal("0.13"),
        )
        updated = auto.apply_position_state([position], AutoConfig())[0]
        self.assertEqual(updated.peak_price, Decimal("120"))
        self.assertEqual(updated.trailing_stop_price, Decimal("115.20"))


class IntelligenceTests(unittest.TestCase):
    def test_news_collector_includes_default_and_extra_sources(self) -> None:
        original_feeds = os.environ.get("AI_EXTRA_NEWS_FEEDS")
        original_queries = os.environ.get("AI_EXTRA_NEWS_QUERIES")
        original_override_feeds = os.environ.get("AI_NEWS_FEEDS")
        original_override_queries = os.environ.get("AI_NEWS_QUERIES")
        os.environ.pop("AI_NEWS_FEEDS", None)
        os.environ.pop("AI_NEWS_QUERIES", None)
        os.environ["AI_EXTRA_NEWS_FEEDS"] = "https://example.test/rss"
        os.environ["AI_EXTRA_NEWS_QUERIES"] = "site:example.test BTC when:7d"
        try:
            collector = NewsCollector()
            urls = collector._feed_urls()
            self.assertTrue(any("coindesk.com" in url for url in urls))
            self.assertTrue(any("cointelegraph.com/rss" in url for url in urls))
            self.assertIn("https://example.test/rss", urls)
            self.assertTrue(any("site%3Aexample.test" in url for url in urls))
        finally:
            _restore_env("AI_EXTRA_NEWS_FEEDS", original_feeds)
            _restore_env("AI_EXTRA_NEWS_QUERIES", original_queries)
            _restore_env("AI_NEWS_FEEDS", original_override_feeds)
            _restore_env("AI_NEWS_QUERIES", original_override_queries)

    def test_keyword_analyzer_scores_market_news(self) -> None:
        analyzer = KeywordInfoAnalyzer()
        signal = analyzer.analyze(
            [
                {
                    "market": "KRW-BTC",
                    "korean_name": "비트코인",
                    "english_name": "Bitcoin",
                }
            ],
            [
                NewsItem(
                    title="Bitcoin ETF approval boosts adoption",
                    link="https://example.test/positive",
                ),
                NewsItem(
                    title="Bitcoin exchange hack investigation warning",
                    link="https://example.test/negative",
                ),
            ],
        )
        self.assertIn("KRW-BTC", signal.market_scores)
        self.assertLess(signal.market_scores["KRW-BTC"], Decimal("0"))
        self.assertIn("KRW-BTC", signal.blocked_markets)


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


class NotifierTests(unittest.TestCase):
    def test_disabled_notifier_returns_no_errors(self) -> None:
        settings = Settings(
            access_key="",
            secret_key="",
            base_url="https://api.upbit.com",
            live_trading=False,
            max_order_krw=Decimal("10000"),
            min_order_krw=Decimal("5000"),
            allow_full_balance_autotrade=False,
            alerts_enabled=False,
            telegram_bot_token="",
            telegram_chat_id="",
            alert_webhook_url="",
        )
        errors = Notifier(settings).send(
            Notification(event="test", title="Test", details={"ok": True})
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
