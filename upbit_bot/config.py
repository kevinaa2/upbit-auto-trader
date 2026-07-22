from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default).strip()
    try:
        return Decimal(raw)
    except Exception as exc:
        raise ValueError(f"{name} must be a decimal value, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    access_key: str
    secret_key: str
    base_url: str
    live_trading: bool
    max_order_krw: Decimal
    min_order_krw: Decimal
    allow_full_balance_autotrade: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            access_key=os.getenv("UPBIT_ACCESS_KEY", "").strip(),
            secret_key=os.getenv("UPBIT_SECRET_KEY", "").strip(),
            base_url=os.getenv("UPBIT_BASE_URL", "https://api.upbit.com").rstrip("/"),
            live_trading=_env_bool("UPBIT_LIVE_TRADING", False),
            max_order_krw=_env_decimal("MAX_ORDER_KRW", "10000"),
            min_order_krw=_env_decimal("MIN_ORDER_KRW", "5000"),
            allow_full_balance_autotrade=_env_bool("AUTO_ALLOW_FULL_BALANCE", False),
        )

    def require_keys(self) -> None:
        if not self.access_key or not self.secret_key:
            raise RuntimeError(
                "UPBIT_ACCESS_KEY and UPBIT_SECRET_KEY must be set for this command."
            )
