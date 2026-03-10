from __future__ import annotations

import shutil
import unittest
from datetime import date, timedelta
from pathlib import Path

from options_trader_poc.config import Settings
from options_trader_poc.repository import Repository
from options_trader_poc.service import TradingService


class FakeTradierClient:
    def __init__(self) -> None:
        self.submissions = []
        self.close_submissions = []
        self.snapshots = {
            "SPY": {"price": 500, "moving_average_50": 490},
            "QQQ": {"price": 500, "moving_average_50": 510},
        }
        expiry = (date.today() + timedelta(days=35)).isoformat()
        self.chains = {
            "SPY": [
                {"option_symbol": "SPYP485", "expiration": expiry, "strike": 485, "option_type": "put", "delta": -0.20, "bid": 1.9, "ask": 2.1, "open_interest": 2000, "volume": 1000},
                {"option_symbol": "SPYP480", "expiration": expiry, "strike": 480, "option_type": "put", "delta": -0.10, "bid": 0.7, "ask": 0.8, "open_interest": 1500, "volume": 900},
                {"option_symbol": "SPYP475", "expiration": expiry, "strike": 475, "option_type": "put", "delta": -0.06, "bid": 0.35, "ask": 0.45, "open_interest": 1400, "volume": 700},
            ],
            "QQQ": [
                {"option_symbol": "QQQC515", "expiration": expiry, "strike": 515, "option_type": "call", "delta": 0.24, "bid": 3.2, "ask": 3.3, "open_interest": 1200, "volume": 300},
                {"option_symbol": "QQQC520", "expiration": expiry, "strike": 520, "option_type": "call", "delta": 0.20, "bid": 2.3, "ask": 2.4, "open_interest": 1100, "volume": 250},
                {"option_symbol": "QQQC525", "expiration": expiry, "strike": 525, "option_type": "call", "delta": 0.14, "bid": 1.5, "ask": 1.6, "open_interest": 900, "volume": 150},
                {"option_symbol": "QQQC530", "expiration": expiry, "strike": 530, "option_type": "call", "delta": 0.10, "bid": 0.8, "ask": 0.9, "open_interest": 850, "volume": 120},
            ],
        }
        self.sync_payload = {"orders": [], "order_details": [], "positions": [], "synced_at": date.today().isoformat()}

    def get_market_snapshot(self, symbol: str):
        return self.snapshots[symbol]

    def get_option_chain_for_target_dte(self, symbol: str):
        return self.chains[symbol]

    def submit_multileg_order(self, candidate):
        self.submissions.append(candidate.symbol)
        return {"order": {"id": f"{candidate.symbol}-12345"}, "status": "ok"}

    def build_multileg_order_payload(self, candidate):
        return {
            "symbol": candidate.symbol,
            "price": candidate.net_credit,
            "option_symbol[0]": candidate.short_leg.option_symbol,
            "option_symbol[1]": candidate.long_leg.option_symbol,
            "side[0]": "sell_to_open",
            "side[1]": "buy_to_open",
        }

    def submit_close_order(self, position, limit_price):
        self.close_submissions.append({"position_id": position.position_id, "limit_price": limit_price})
        return {"order": {"id": f"close-{position.position_id}"}, "status": "ok"}

    def build_close_order_payload(self, position, limit_price):
        return {
            "symbol": position.symbol,
            "price": limit_price,
            "type": "debit",
            "option_symbol[0]": position.short_option_symbol,
            "option_symbol[1]": position.long_option_symbol,
            "side[0]": "buy_to_close",
            "side[1]": "sell_to_close",
        }

    def sync_orders(self, order_ids=None):
        return self.sync_payload

    def estimate_position_close_debit(self, position):
        return 0.40


class RepoTempTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path(".test_tmp") / self.__class__.__name__ / self._testMethodName
        shutil.rmtree(self.temp_root, ignore_errors=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def make_service(self, **setting_overrides) -> TradingService:
        db_path = self.temp_root / "test.db"
        defaults = {"decisions_per_symbol": 1, "spread_widths": (5,)}
        defaults.update(setting_overrides)
        settings = Settings(
            db_path=db_path,
            universe_symbols=("SPY", "QQQ"),
            tradier_account_id="paper",
            tradier_access_token="token",
            auto_submit_paper=True,
            **defaults,
        )
        repository = Repository(db_path)
        tradier = FakeTradierClient()
        return TradingService(settings, repository, tradier)


class ServiceTests(RepoTempTestCase):
    def test_scan_creates_trade_decision(self) -> None:
        service = self.make_service()
        response = service.scan_universe_live()
        opens = [decision for decision in response if decision.action.value == "open"]
        self.assertEqual(len(opens), 2)

    def test_scan_can_emit_multiple_decisions_per_symbol(self) -> None:
        service = self.make_service(decisions_per_symbol=2, spread_widths=(5, 10))
        response = service.scan_universe_live()
        qqq_opens = [decision for decision in response if decision.symbol == "QQQ" and decision.action.value == "open"]
        self.assertGreaterEqual(len(qqq_opens), 2)

    def test_run_automated_cycle_submits_orders(self) -> None:
        service = self.make_service()
        result = service.run_automated_cycle(auto_submit=True)
        self.assertEqual(len(result["submissions"]), 2)
        self.assertTrue(all(item["status"] == "submitted" for item in result["submissions"]))

    def test_submit_trade_persists_position(self) -> None:
        service = self.make_service()
        decisions = service.scan_universe_live()
        decision = next(item for item in decisions if item.action.value == "open")
        result = service.submit_trade(decision.decision_id)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(len(service.repository.list_open_positions()), 1)

    def test_evaluate_exits_submits_close_order(self) -> None:
        service = self.make_service()
        decision = service.scan_universe_live()[0]
        submission = service.submit_trade(decision.decision_id)
        position_id = submission["position_id"]
        evaluations = service.evaluate_exits({position_id: 0.40}, auto_submit=True)
        self.assertEqual(evaluations[0].action, "close")
        self.assertEqual(evaluations[0].submission_status, "submitted")
        self.assertTrue(evaluations[0].broker_order_id.startswith("close-"))
        self.assertEqual(len(service.tradier_client.close_submissions), 1)

    def test_evaluate_exits_can_skip_close_submission(self) -> None:
        service = self.make_service()
        decision = service.scan_universe_live()[0]
        submission = service.submit_trade(decision.decision_id)
        position_id = submission["position_id"]
        evaluations = service.evaluate_exits({position_id: 0.40}, auto_submit=False)
        self.assertEqual(evaluations[0].action, "close")
        self.assertEqual(evaluations[0].submission_status, "not_submitted")
        self.assertEqual(len(service.tradier_client.close_submissions), 0)

    def test_sync_marks_canceled_entry_as_canceled_position(self) -> None:
        service = self.make_service()
        decision = service.scan_universe_live()[0]
        submission = service.submit_trade(decision.decision_id)
        service.tradier_client.sync_payload = {
            "orders": [],
            "order_details": [{"id": submission["broker_order_id"], "status": "canceled", "raw": {"id": submission["broker_order_id"], "status": "canceled"}}],
            "positions": [],
            "synced_at": date.today().isoformat(),
        }
        result = service.sync_orders()
        position = service.repository.list_positions()[0]
        order = service.repository.list_orders()[0]
        self.assertEqual(position.status.value, "canceled")
        self.assertEqual(order["status"], "canceled")
        self.assertEqual(result["reconciliation"]["position_updates"][0]["to"], "canceled")

    def test_sync_marks_live_broker_legs_as_open(self) -> None:
        service = self.make_service()
        decision = service.scan_universe_live()[0]
        submission = service.submit_trade(decision.decision_id)
        position = service.repository.list_positions()[0]
        service.tradier_client.sync_payload = {
            "orders": [],
            "order_details": [{"id": submission["broker_order_id"], "status": "filled", "raw": {"id": submission["broker_order_id"], "status": "filled"}}],
            "positions": [
                {"option_symbol": position.short_option_symbol, "raw": {}},
                {"option_symbol": position.long_option_symbol, "raw": {}},
            ],
            "synced_at": date.today().isoformat(),
        }
        service.sync_orders()
        refreshed = service.repository.list_positions()[0]
        self.assertEqual(refreshed.status.value, "open")


if __name__ == "__main__":
    unittest.main()
