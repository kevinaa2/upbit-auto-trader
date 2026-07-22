from __future__ import annotations

import unittest
from decimal import Decimal

from upbit_bot.auto_trader import AutoConfig, AutoTrader
from upbit_bot.config import Settings
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


if __name__ == "__main__":
    unittest.main()
