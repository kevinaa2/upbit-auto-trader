from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen


class UpbitApiError(RuntimeError):
    def __init__(self, status: int | None, message: str, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


@dataclass(frozen=True)
class UpbitResponse:
    data: Any
    status: int
    remaining_req: str | None


class UpbitClient:
    def __init__(
        self,
        access_key: str = "",
        secret_key: str = "",
        base_url: str = "https://api.upbit.com",
        timeout: float = 10.0,
    ) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_ticker(self, market: str) -> UpbitResponse:
        query = self._query_string({"markets": market})
        return self._request("GET", f"/v1/ticker?{query}", auth=False)

    def get_tickers(self, markets: list[str]) -> UpbitResponse:
        query = self._query_string({"markets": ",".join(markets)})
        return self._request("GET", f"/v1/ticker?{query}", auth=False)

    def get_markets(self, is_details: bool = True) -> UpbitResponse:
        query = self._query_string({"is_details": str(is_details).lower()})
        return self._request("GET", f"/v1/market/all?{query}", auth=False)

    def get_accounts(self) -> UpbitResponse:
        return self._request("GET", "/v1/accounts", auth=True)

    def create_order(self, payload: dict[str, str]) -> UpbitResponse:
        return self._request("POST", "/v1/orders", body=payload, auth=True)

    def get_order(self, uuid_value: str | None = None, identifier: str | None = None) -> UpbitResponse:
        params: dict[str, str] = {}
        if uuid_value:
            params["uuid"] = uuid_value
        if identifier:
            params["identifier"] = identifier
        if not params:
            raise ValueError("uuid_value or identifier is required")
        query = self._query_string(params)
        return self._request("GET", f"/v1/order?{query}", query_string=query, auth=True)

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, str] | None = None,
        query_string: str = "",
        auth: bool = False,
    ) -> UpbitResponse:
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        data: bytes | None = None

        auth_query_string = query_string
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            auth_query_string = self._query_string(body)

        if auth:
            token = self._jwt(auth_query_string)
            headers["Authorization"] = f"Bearer {token}"

        request = Request(url, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                parsed = json.loads(raw) if raw else None
                return UpbitResponse(
                    data=parsed,
                    status=response.status,
                    remaining_req=response.headers.get("Remaining-Req"),
                )
        except HTTPError as exc:
            payload = self._read_error_payload(exc)
            message = self._error_message(exc.code, payload)
            raise UpbitApiError(exc.code, message, payload) from exc
        except URLError as exc:
            raise UpbitApiError(None, f"Network error: {exc.reason}") from exc

    def _jwt(self, query_string: str = "") -> str:
        if not self.access_key or not self.secret_key:
            raise RuntimeError("Upbit API keys are required for authenticated requests.")

        header = {"alg": "HS512", "typ": "JWT"}
        payload: dict[str, str] = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }
        if query_string:
            payload["query_hash"] = hashlib.sha512(query_string.encode("utf-8")).hexdigest()
            payload["query_hash_alg"] = "SHA512"

        signing_input = ".".join(
            [
                self._base64url_json(header),
                self._base64url_json(payload),
            ]
        )
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            signing_input.encode("utf-8"),
            hashlib.sha512,
        ).digest()
        return f"{signing_input}.{self._base64url(signature)}"

    @staticmethod
    def _query_string(params: dict[str, Any]) -> str:
        return unquote(urlencode(params, doseq=True))

    @staticmethod
    def _base64url_json(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":"), sort_keys=False).encode("utf-8")
        return UpbitClient._base64url(raw)

    @staticmethod
    def _base64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _read_error_payload(exc: HTTPError) -> Any:
        raw = exc.read().decode("utf-8", errors="replace")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @staticmethod
    def _error_message(status: int, payload: Any) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                name = error.get("name", "upbit_error")
                message = error.get("message", "")
                return f"Upbit API error {status}: {name} {message}".strip()
        if status in {418, 429}:
            time.sleep(1)
        return f"Upbit API error {status}: {payload}"
