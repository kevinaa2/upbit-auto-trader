from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import Settings


@dataclass(frozen=True)
class Notification:
    event: str
    title: str
    level: str = "info"
    details: dict[str, Any] = field(default_factory=dict)


class Notifier:
    def __init__(self, settings: Settings, timeout: float = 8.0) -> None:
        self.settings = settings
        self.timeout = timeout

    def enabled(self) -> bool:
        return self.settings.alerts_enabled and (
            self._telegram_configured() or bool(self.settings.alert_webhook_url)
        )

    def send(self, notification: Notification) -> list[str]:
        if not self.enabled():
            return []
        errors: list[str] = []
        if self._telegram_configured():
            try:
                self._send_telegram(notification)
            except Exception as exc:
                errors.append(f"telegram: {exc}")
        if self.settings.alert_webhook_url:
            try:
                self._send_webhook(notification)
            except Exception as exc:
                errors.append(f"webhook: {exc}")
        return errors

    def _telegram_configured(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id)

    def _send_telegram(self, notification: Notification) -> None:
        token = self.settings.telegram_bot_token
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = urlencode(
            {
                "chat_id": self.settings.telegram_chat_id,
                "text": self._message(notification),
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            response.read()

    def _send_webhook(self, notification: Notification) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": notification.event,
            "title": notification.title,
            "level": notification.level,
            "details": notification.details,
        }
        request = Request(
            self.settings.alert_webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            response.read()

    def _message(self, notification: Notification) -> str:
        lines = [
            f"[upbit-auto-trader] {notification.title}",
            f"event: {notification.event}",
            f"level: {notification.level}",
        ]
        for key, value in notification.details.items():
            lines.append(f"{key}: {self._compact(value)}")
        return "\n".join(lines)[:3500]

    @staticmethod
    def _compact(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:800]
        return str(value)[:800]
