from __future__ import annotations

import unittest
from datetime import date, timedelta

from options_trader_poc.config import Settings
from options_trader_poc.tradier import TradierClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeTransport:
    def __init__(self, expiry: str, second_expiry: str) -> None:
        self.expiry = expiry
        self.second_expiry = second_expiry
        self.calls = []

    def request(self, method, url, headers, params=None, data=None):
        self.calls.append({"method": method, "url": url, "params": params, "data": data})
        if url.endswith("/markets/quotes"):
            symbols = params["symbols"].split(",")
            quotes = []
            for symbol in symbols:
                if symbol == "SPY":
                    quotes.append({"symbol": "SPY", "last": 510.0, "ask": 510.1, "bid": 509.9})
                elif symbol == "SPYP445":
                    quotes.append({"symbol": "SPYP445", "last": 1.2, "ask": 1.3, "bid": 1.1})
                elif symbol == "SPYP440":
                    quotes.append({"symbol": "SPYP440", "last": 0.6, "ask": 0.7, "bid": 0.5})
            quote_payload = quotes[0] if len(quotes) == 1 else quotes
            return FakeResponse({"quotes": {"quote": quote_payload}})
        if url.endswith("/markets/history"):
            return FakeResponse({"history": {"day": [{"close": float(500 + idx)} for idx in range(60)]}})
        if url.endswith("/markets/options/expirations"):
            return FakeResponse({"expirations": {"date": [self.expiry, self.second_expiry]}})
        if url.endswith("/markets/options/chains"):
            expiry = params["expiration"]
            if expiry == self.expiry:
                return FakeResponse({
                    "options": {
                        "option": [
                            {"symbol": "SPYP445", "expiration_date": self.expiry, "strike": 445, "option_type": "put", "greeks": {"delta": -0.20}, "bid": 1.9, "ask": 2.1, "open_interest": 2000, "volume": 1000},
                            {"symbol": "SPYP440", "expiration_date": self.expiry, "strike": 440, "option_type": "put", "greeks": {}, "bid": 0.7, "ask": 0.8, "open_interest": 1500, "volume": 900},
                        ]
                    }
                })
            return FakeResponse({
                "options": {
                    "option": [
                        {"symbol": "SPYP450", "expiration_date": self.second_expiry, "strike": 450, "option_type": "put", "greeks": {"delta": -0.18}, "bid": 1.5, "ask": 1.7, "open_interest": 1200, "volume": 500},
                        {"symbol": "SPYP445B", "expiration_date": self.second_expiry, "strike": 445, "option_type": "put", "greeks": {}, "bid": 0.8, "ask": 0.9, "open_interest": 1000, "volume": 400},
                    ]
                }
            })
        if url.endswith("/accounts/paper/orders"):
            return FakeResponse({"orders": {"order": [{"id": 101, "status": "open", "leg": [{"option_symbol": "SPYP445"}, {"option_symbol": "SPYP440"}]}]}})
        if url.endswith("/accounts/paper/orders/101"):
            return FakeResponse({"order": {"id": 101, "status": "canceled", "leg": [{"option_symbol": "SPYP445"}, {"option_symbol": "SPYP440"}]}})
        if url.endswith("/accounts/paper/positions"):
            return FakeResponse({"positions": {"position": [{"symbol": "SPY", "option_symbol": "SPYP445", "quantity": -1}, {"symbol": "SPY", "option_symbol": "SPYP440", "quantity": 1}]}})
        return FakeResponse({})


class TradierClientTests(unittest.TestCase):
    def test_market_snapshot_and_chain_normalization(self) -> None:
        expiry = (date.today() + timedelta(days=35)).isoformat()
        second_expiry = (date.today() + timedelta(days=42)).isoformat()
        client = TradierClient(
            Settings(tradier_access_token="token", tradier_account_id="paper"),
            transport=FakeTransport(expiry, second_expiry),
        )
        snapshot = client.get_market_snapshot("SPY")
        chain = client.get_option_chain_for_target_dte("SPY")
        self.assertEqual(snapshot["price"], 510.0)
        self.assertTrue(snapshot["moving_average_50"] > 0)
        self.assertEqual(chain[0]["option_symbol"], "SPYP445")
        self.assertEqual(chain[0]["delta"], -0.2)
        self.assertEqual(len(chain), 4)

    def test_sync_orders_normalizes_order_details_and_positions(self) -> None:
        expiry = (date.today() + timedelta(days=35)).isoformat()
        second_expiry = (date.today() + timedelta(days=42)).isoformat()
        client = TradierClient(
            Settings(tradier_access_token="token", tradier_account_id="paper"),
            transport=FakeTransport(expiry, second_expiry),
        )
        payload = client.sync_orders(["101"])
        self.assertEqual(payload["orders"][0]["status"], "open")
        self.assertEqual(payload["order_details"][0]["status"], "canceled")
        self.assertEqual(payload["positions"][0]["option_symbol"], "SPYP445")


if __name__ == "__main__":
    unittest.main()
