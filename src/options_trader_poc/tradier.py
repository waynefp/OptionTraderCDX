from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from .config import Settings
from .models import Position, SpreadCandidate


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        ...


class MissingTradierDependencyError(RuntimeError):
    pass


class DefaultHttpTransport:
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        try:
            import httpx
        except ModuleNotFoundError as exc:
            raise MissingTradierDependencyError("httpx is required for live Tradier requests") from exc

        with httpx.Client(timeout=60.0) as client:
            return client.request(method=method, url=url, headers=headers, params=params, data=data)


@dataclass(slots=True)
class TradierClient:
    settings: Settings
    transport: HttpTransport | None = None

    def __post_init__(self) -> None:
        if self.transport is None:
            self.transport = DefaultHttpTransport()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.tradier_access_token}",
            "Accept": "application/json",
        }

    def get_market_snapshot(self, symbol: str) -> dict[str, Any]:
        quote = self.get_quote(symbol)
        moving_average_50 = self.get_moving_average(symbol, 50)
        return {
            "price": self._extract_quote_price(quote),
            "moving_average_50": moving_average_50,
            "as_of": datetime.now(UTC).isoformat(),
        }

    def get_quote(self, symbol: str) -> dict[str, Any]:
        payload = self._request_json("GET", "/markets/quotes", params={"symbols": symbol, "greeks": "false"})
        quotes = payload.get("quotes", {}).get("quote")
        if isinstance(quotes, list):
            return quotes[0]
        if isinstance(quotes, dict):
            return quotes
        raise ValueError(f"No quote returned for {symbol}")

    def get_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        payload = self._request_json("GET", "/markets/quotes", params={"symbols": ",".join(symbols), "greeks": "false"})
        quotes = payload.get("quotes", {}).get("quote")
        normalized = self._as_list(quotes)
        return {item.get("symbol") or item.get("option_symbol"): item for item in normalized}

    def get_moving_average(self, symbol: str, window: int) -> float:
        end_date = date.today()
        start_date = end_date - timedelta(days=max(window * 3, 90))
        payload = self._request_json(
            "GET",
            "/markets/history",
            params={
                "symbol": symbol,
                "interval": "daily",
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
        )
        days = self._as_list(payload.get("history", {}).get("day"))
        closes = [float(day["close"]) for day in days if day.get("close") is not None]
        if len(closes) < window:
            raise ValueError(f"Not enough history returned to compute {window}-day moving average for {symbol}")
        return round(sum(closes[-window:]) / window, 4)

    def get_option_chain_for_target_dte(self, symbol: str) -> list[dict[str, Any]]:
        expirations = self.list_entry_expirations(symbol, self.settings.min_entry_dte, self.settings.max_entry_dte)
        if not expirations:
            raise ValueError(f"No expiration found for {symbol} between {self.settings.min_entry_dte} and {self.settings.max_entry_dte} DTE")
        return self.get_option_chains_for_expirations(symbol, expirations)

    def list_entry_expirations(self, symbol: str, min_dte: int, max_dte: int) -> list[date]:
        payload = self._request_json(
            "GET",
            "/markets/options/expirations",
            params={"symbol": symbol, "includeAllRoots": "true", "strikes": "false"},
        )
        entries = self._as_list(payload.get("expirations", {}).get("date"))
        candidates: list[date] = []
        for entry in entries:
            raw = entry.get("date") if isinstance(entry, dict) else entry
            if raw:
                candidates.append(date.fromisoformat(str(raw)))
        return sorted(expiry for expiry in candidates if min_dte <= (expiry - date.today()).days <= max_dte)

    def get_option_chains_for_expirations(self, symbol: str, expirations: list[date]) -> list[dict[str, Any]]:
        aggregated: list[dict[str, Any]] = []
        for expiration in expirations:
            payload = self._request_json(
                "GET",
                "/markets/options/chains",
                params={"symbol": symbol, "expiration": expiration.isoformat(), "greeks": "true"},
            )
            options = self._as_list(payload.get("options", {}).get("option"))
            aggregated.extend(self._normalize_option_contract(symbol, item) for item in options)
        return aggregated

    def build_multileg_order_payload(self, candidate: SpreadCandidate) -> dict[str, Any]:
        return {
            "class": "multileg",
            "symbol": candidate.symbol,
            "type": "credit",
            "duration": "day",
            "price": f"{candidate.net_credit:.2f}",
            "option_symbol[0]": candidate.short_leg.option_symbol,
            "side[0]": "sell_to_open",
            "quantity[0]": candidate.quantity,
            "option_symbol[1]": candidate.long_leg.option_symbol,
            "side[1]": "buy_to_open",
            "quantity[1]": candidate.quantity,
        }

    def submit_multileg_order(self, candidate: SpreadCandidate) -> dict[str, Any]:
        self._ensure_credentials()
        return self._request_json(
            "POST",
            f"/accounts/{self.settings.tradier_account_id}/orders",
            data=self.build_multileg_order_payload(candidate),
        )

    def submit_close_order(self, position: Position, limit_price: float) -> dict[str, Any]:
        self._ensure_credentials()
        return self._request_json(
            "POST",
            f"/accounts/{self.settings.tradier_account_id}/orders",
            data=self.build_close_order_payload(position, limit_price),
        )

    def sync_orders(self, order_ids: list[str] | None = None) -> dict[str, Any]:
        if not self.settings.tradier_account_id or not self.settings.tradier_access_token:
            return {"orders": [], "order_details": [], "positions": [], "synced_at": datetime.now(UTC).isoformat(), "note": "missing Tradier credentials", "notes": []}

        notes: list[str] = []

        try:
            open_orders_payload = self._request_json("GET", f"/accounts/{self.settings.tradier_account_id}/orders")
            open_orders = self._normalize_orders_payload(open_orders_payload)
        except Exception as exc:
            open_orders = []
            notes.append(f"orders_error: {exc}")

        try:
            positions_payload = self._request_json("GET", f"/accounts/{self.settings.tradier_account_id}/positions")
            positions = self._normalize_positions_payload(positions_payload)
        except Exception as exc:
            positions = []
            notes.append(f"positions_error: {exc}")

        order_details = []
        for order_id in order_ids or []:
            try:
                detail_payload = self._request_json("GET", f"/accounts/{self.settings.tradier_account_id}/orders/{order_id}")
                order = self._extract_order(detail_payload)
                if order:
                    order_details.append(self._normalize_order(order))
                else:
                    order_details.append({"id": str(order_id), "status": "unknown", "raw": {"note": "missing order payload"}})
            except Exception as exc:
                order_details.append({"id": str(order_id), "status": "unknown", "raw": {"error": str(exc)}})
                notes.append(f"order_detail_error[{order_id}]: {exc}")

        return {
            "orders": open_orders,
            "order_details": order_details,
            "positions": positions,
            "synced_at": datetime.now(UTC).isoformat(),
            "notes": notes,
        }

    def estimate_position_close_debit(self, position: Position) -> float | None:
        quotes = self.get_quotes([position.short_option_symbol, position.long_option_symbol])
        short_quote = quotes.get(position.short_option_symbol)
        long_quote = quotes.get(position.long_option_symbol)
        if not short_quote or not long_quote:
            return None
        short_close = self._extract_quote_price(short_quote, prefer_ask=True)
        long_close = self._extract_quote_price(long_quote, prefer_bid=True)
        return round(max(short_close - long_close, 0.01), 2)

    def build_close_order_payload(self, position: Position, limit_price: float) -> dict[str, Any]:
        return {
            "class": "multileg",
            "symbol": position.symbol,
            "type": "debit",
            "duration": "day",
            "price": f"{limit_price:.2f}",
            "option_symbol[0]": position.short_option_symbol,
            "side[0]": "buy_to_close",
            "quantity[0]": position.quantity,
            "option_symbol[1]": position.long_option_symbol,
            "side[1]": "sell_to_close",
            "quantity[1]": position.quantity,
        }

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.settings.tradier_access_token:
            raise ValueError("Tradier access token is required")
        response = self.transport.request(
            method,
            f"{self.settings.tradier_base_url}{path}",
            headers=self._headers,
            params=params,
            data=data,
        )
        if hasattr(response, "status_code") and int(response.status_code) >= 400:
            detail = response.text if hasattr(response, "text") else "Tradier request failed"
            raise ValueError(f"Tradier API error {response.status_code}: {detail}")
        if hasattr(response, "json"):
            return response.json()
        if isinstance(response, dict):
            return response
        raise TypeError("Unexpected Tradier response type")

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    @staticmethod
    def _extract_quote_price(quote: dict[str, Any], prefer_ask: bool = False, prefer_bid: bool = False) -> float:
        keys = ["last", "close", "ask", "bid"]
        if prefer_ask:
            keys = ["ask", "last", "close", "bid"]
        elif prefer_bid:
            keys = ["bid", "last", "close", "ask"]
        for key in keys:
            value = quote.get(key)
            if value is not None:
                return float(value)
        raise ValueError("No usable price found in quote payload")

    @staticmethod
    def _extract_delta(option_payload: dict[str, Any]) -> float | None:
        greeks = option_payload.get("greeks") or {}
        delta = greeks.get("delta") if isinstance(greeks, dict) else option_payload.get("delta")
        if delta in (None, ""):
            return None
        return float(delta)

    def _normalize_option_contract(self, symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
        option_symbol = payload.get("symbol") or payload.get("option_symbol")
        option_type = str(payload.get("option_type") or payload.get("type") or "").lower()
        if option_type not in {"call", "put"}:
            raw = str(payload.get("option_type") or payload.get("type") or "").upper()
            option_type = "call" if raw.startswith("C") else "put"
        return {
            "option_symbol": option_symbol,
            "expiration": payload["expiration_date"],
            "strike": float(payload["strike"]),
            "option_type": option_type,
            "delta": self._extract_delta(payload),
            "bid": float(payload.get("bid") or 0.0),
            "ask": float(payload.get("ask") or 0.0),
            "open_interest": int(payload.get("open_interest") or 0),
            "volume": int(payload.get("volume") or 0),
        }

    def _normalize_orders_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        orders = payload.get("orders", {}).get("order")
        return [self._normalize_order(item) for item in self._as_list(orders)]

    def _normalize_positions_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        positions = payload.get("positions", {}).get("position")
        normalized = []
        for item in self._as_list(positions):
            if not item:
                continue
            normalized.append(
                {
                    "symbol": item.get("symbol"),
                    "option_symbol": item.get("option_symbol") or item.get("symbol"),
                    "quantity": item.get("quantity"),
                    "cost_basis": item.get("cost_basis"),
                    "raw": item,
                }
            )
        return normalized

    def _normalize_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        order_id = payload.get("id")
        status = self._normalize_order_status(payload.get("status"))
        legs = []
        if isinstance(payload.get("leg"), list):
            legs = [leg.get("option_symbol") for leg in payload.get("leg", []) if isinstance(leg, dict)]
        elif isinstance(payload.get("leg"), dict):
            option_symbol = payload["leg"].get("option_symbol")
            if option_symbol:
                legs = [option_symbol]
        return {
            "id": str(order_id) if order_id is not None else None,
            "status": status,
            "tag": str(payload.get("tag") or "").lower(),
            "legs": [leg for leg in legs if leg],
            "raw": payload,
        }

    def _extract_order(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if "order" in payload and isinstance(payload["order"], dict):
            return payload["order"]
        orders = payload.get("orders", {}).get("order")
        entries = self._as_list(orders)
        return entries[0] if entries else None

    @staticmethod
    def _normalize_order_status(status: Any) -> str:
        normalized = str(status or "unknown").strip().lower().replace(" ", "_")
        if normalized == "ok":
            return "submitted"
        if normalized == "cancelled":
            return "canceled"
        return normalized

    def _ensure_credentials(self) -> None:
        if not self.settings.tradier_account_id or not self.settings.tradier_access_token:
            raise ValueError("Tradier credentials are required before submitting orders")
