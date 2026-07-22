from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .config import Settings
from .upbit_client import UpbitClient, UpbitResponse


@dataclass(frozen=True)
class OrderPlan:
    payload: dict[str, str]
    live: bool


class Trader:
    def __init__(self, settings: Settings, client: UpbitClient | None = None) -> None:
        self.settings = settings
        self.client = client or UpbitClient(
            access_key=settings.access_key,
            secret_key=settings.secret_key,
            base_url=settings.base_url,
        )

    def ticker(self, market: str) -> UpbitResponse:
        return self.client.get_ticker(self._market(market))

    def tickers(self, markets: list[str]) -> UpbitResponse:
        if not markets:
            raise ValueError("at least one market is required")
        return self.client.get_tickers([self._market(market) for market in markets])

    def markets(self, quote: str = "KRW", include_warnings: bool = False) -> UpbitResponse:
        response = self.client.get_markets(is_details=True)
        prefix = quote.strip().upper() + "-"
        markets = []
        for item in response.data:
            market = str(item.get("market", "")).upper()
            warning = str(item.get("market_warning", "NONE")).upper()
            if not market.startswith(prefix):
                continue
            if not include_warnings and warning != "NONE":
                continue
            markets.append(item)
        return UpbitResponse(markets, response.status, response.remaining_req)

    def balances(self) -> UpbitResponse:
        self.settings.require_keys()
        return self.client.get_accounts()

    def market_buy(self, market: str, krw: str, live: bool, yes: bool) -> UpbitResponse | OrderPlan:
        amount = self._decimal(krw, "krw")
        self._validate_krw_amount(amount)
        payload = {
            "market": self._market(market),
            "side": "bid",
            "ord_type": "price",
            "price": self._decimal_string(amount),
            "identifier": self._identifier("buy"),
        }
        return self._place_or_plan(payload, live, yes)

    def market_sell(self, market: str, volume: str, live: bool, yes: bool) -> UpbitResponse | OrderPlan:
        amount = self._decimal(volume, "volume")
        if amount <= 0:
            raise ValueError("volume must be greater than 0")
        payload = {
            "market": self._market(market),
            "side": "ask",
            "ord_type": "market",
            "volume": self._decimal_string(amount),
            "identifier": self._identifier("sell"),
        }
        return self._place_or_plan(payload, live, yes)

    def limit_buy(
        self,
        market: str,
        volume: str,
        price: str,
        live: bool,
        yes: bool,
    ) -> UpbitResponse | OrderPlan:
        vol = self._decimal(volume, "volume")
        unit_price = self._decimal(price, "price")
        if vol <= 0 or unit_price <= 0:
            raise ValueError("volume and price must be greater than 0")
        self._validate_krw_amount(vol * unit_price)
        payload = {
            "market": self._market(market),
            "side": "bid",
            "ord_type": "limit",
            "volume": self._decimal_string(vol),
            "price": self._decimal_string(unit_price),
            "identifier": self._identifier("limit-buy"),
        }
        return self._place_or_plan(payload, live, yes)

    def limit_sell(
        self,
        market: str,
        volume: str,
        price: str,
        live: bool,
        yes: bool,
    ) -> UpbitResponse | OrderPlan:
        vol = self._decimal(volume, "volume")
        unit_price = self._decimal(price, "price")
        if vol <= 0 or unit_price <= 0:
            raise ValueError("volume and price must be greater than 0")
        payload = {
            "market": self._market(market),
            "side": "ask",
            "ord_type": "limit",
            "volume": self._decimal_string(vol),
            "price": self._decimal_string(unit_price),
            "identifier": self._identifier("limit-sell"),
        }
        return self._place_or_plan(payload, live, yes)

    def _place_or_plan(self, payload: dict[str, str], live: bool, yes: bool) -> UpbitResponse | OrderPlan:
        if not live:
            return OrderPlan(payload=payload, live=False)
        if not self.settings.live_trading:
            raise RuntimeError("Set UPBIT_LIVE_TRADING=true before sending real orders.")
        if not yes:
            raise RuntimeError("Real orders require --yes.")
        self.settings.require_keys()
        return self.client.create_order(payload)

    def _validate_krw_amount(self, amount: Decimal) -> None:
        if amount < self.settings.min_order_krw:
            raise ValueError(f"order amount must be at least {self.settings.min_order_krw} KRW")
        if self.settings.max_order_krw > 0 and amount > self.settings.max_order_krw:
            raise ValueError(f"order amount exceeds MAX_ORDER_KRW={self.settings.max_order_krw}")

    @staticmethod
    def _market(market: str) -> str:
        normalized = market.strip().upper()
        if "-" not in normalized:
            raise ValueError("market must look like KRW-BTC")
        quote, base = normalized.split("-", 1)
        if not quote or not base:
            raise ValueError("market must look like KRW-BTC")
        return normalized

    @staticmethod
    def _decimal(value: str, name: str) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{name} must be a decimal value") from exc
        if amount.is_nan() or amount.is_infinite():
            raise ValueError(f"{name} must be finite")
        return amount

    @staticmethod
    def _decimal_string(value: Decimal) -> str:
        normalized = value.normalize()
        return format(normalized, "f")

    @staticmethod
    def _identifier(prefix: str) -> str:
        import uuid

        return f"codex-{prefix}-{uuid.uuid4().hex[:24]}"
