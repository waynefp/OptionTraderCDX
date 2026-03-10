from __future__ import annotations

import shutil
import unittest
from datetime import date, timedelta
from pathlib import Path

from options_trader_poc.config import Settings
from options_trader_poc.models import OptionContract, Position, QuoteSnapshot, Regime, StrategyType
from options_trader_poc.repository import Repository
from options_trader_poc.risk import RiskEngine
from options_trader_poc.strategy import StrategyEngine, determine_regime


def make_settings(db_path: Path, **overrides) -> Settings:
    defaults = {"decisions_per_symbol": 1, "spread_widths": (5,)}
    defaults.update(overrides)
    return Settings(db_path=db_path, **defaults)


def sample_chain(symbol: str, expiry: date, include_delta: bool = True) -> list[OptionContract]:
    put_delta = -0.20 if include_delta else None
    call_delta = 0.20 if include_delta else None
    return [
        OptionContract(symbol, f"{symbol}P485", expiry, 485, "put", put_delta, 1.90, 2.10, 2000, 1500),
        OptionContract(symbol, f"{symbol}P480", expiry, 480, "put", -0.10 if include_delta else None, 0.70, 0.80, 1800, 1400),
        OptionContract(symbol, f"{symbol}P475", expiry, 475, "put", -0.06 if include_delta else None, 0.35, 0.45, 1400, 900),
        OptionContract(symbol, f"{symbol}C515", expiry, 515, "call", 0.24 if include_delta else call_delta, 3.20, 3.30, 1200, 300),
        OptionContract(symbol, f"{symbol}C520", expiry, 520, "call", call_delta, 2.30, 2.40, 1100, 250),
        OptionContract(symbol, f"{symbol}C525", expiry, 525, "call", 0.14 if include_delta else None, 1.50, 1.60, 900, 150),
        OptionContract(symbol, f"{symbol}C530", expiry, 530, "call", 0.10 if include_delta else None, 0.80, 0.90, 850, 120),
    ]


class RepoTempTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path(".test_tmp") / self.__class__.__name__ / self._testMethodName
        shutil.rmtree(self.temp_root, ignore_errors=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)


class StrategyTests(RepoTempTestCase):
    def test_determine_regime(self) -> None:
        bullish = QuoteSnapshot(symbol="SPY", price=510, moving_average_50=500)
        bearish = QuoteSnapshot(symbol="SPY", price=490, moving_average_50=500)
        neutral = QuoteSnapshot(symbol="SPY", price=500.5, moving_average_50=500)
        self.assertEqual(determine_regime(bullish), Regime.BULLISH)
        self.assertEqual(determine_regime(bearish), Regime.BEARISH)
        self.assertEqual(determine_regime(neutral), Regime.NEUTRAL)

    def test_selects_bull_put_spread(self) -> None:
        settings = make_settings(self.temp_root / "test.db")
        engine = StrategyEngine(settings)
        snapshot = QuoteSnapshot(symbol="SPY", price=500, moving_average_50=490)
        candidate = engine.select_credit_spread(snapshot, sample_chain("SPY", date.today() + timedelta(days=35)), Regime.BULLISH, 2)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.strategy_type, StrategyType.BULL_PUT_CREDIT_SPREAD)
        self.assertEqual(candidate.quantity, 2)
        self.assertEqual(candidate.width, 5)

    def test_selects_bear_call_spread(self) -> None:
        settings = make_settings(self.temp_root / "test.db")
        engine = StrategyEngine(settings)
        snapshot = QuoteSnapshot(symbol="QQQ", price=500, moving_average_50=510)
        candidate = engine.select_credit_spread(snapshot, sample_chain("QQQ", date.today() + timedelta(days=35)), Regime.BEARISH, 1)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.strategy_type, StrategyType.BEAR_CALL_CREDIT_SPREAD)

    def test_finds_multiple_candidates_for_same_symbol(self) -> None:
        settings = make_settings(self.temp_root / "test.db", spread_widths=(5, 10))
        engine = StrategyEngine(settings)
        snapshot = QuoteSnapshot(symbol="QQQ", price=500, moving_average_50=510)
        candidates = engine.find_credit_spread_candidates(
            snapshot,
            sample_chain("QQQ", date.today() + timedelta(days=35)),
            Regime.BEARISH,
            quantity=1,
            max_candidates=3,
        )
        self.assertGreaterEqual(len(candidates), 2)
        self.assertNotEqual(candidates[0].long_leg.option_symbol, candidates[1].long_leg.option_symbol)

    def test_falls_back_when_delta_missing(self) -> None:
        settings = make_settings(self.temp_root / "test.db")
        engine = StrategyEngine(settings)
        snapshot = QuoteSnapshot(symbol="SPY", price=500, moving_average_50=490)
        candidate = engine.select_credit_spread(snapshot, sample_chain("SPY", date.today() + timedelta(days=35), include_delta=False), Regime.BULLISH, 1)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertIn("fallback strike", " ".join(candidate.rationale))

    def test_rejects_illiquid_contracts(self) -> None:
        settings = make_settings(self.temp_root / "test.db")
        engine = StrategyEngine(settings)
        snapshot = QuoteSnapshot(symbol="SPY", price=500, moving_average_50=490)
        expiry = date.today() + timedelta(days=35)
        illiquid_chain = [
            OptionContract("SPY", "SPYP485", expiry, 485, "put", -0.20, 1.90, 2.80, 10, 2),
            OptionContract("SPY", "SPYP480", expiry, 480, "put", -0.10, 0.70, 0.80, 5, 1),
        ]
        candidate = engine.select_credit_spread(snapshot, illiquid_chain, Regime.BULLISH, 1)
        self.assertIsNone(candidate)

    def test_prefers_target_strike_region(self) -> None:
        settings = make_settings(self.temp_root / "test.db")
        engine = StrategyEngine(settings)
        snapshot = QuoteSnapshot(symbol="SPY", price=500, moving_average_50=490)
        expiry = date.today() + timedelta(days=35)
        chain = [
            OptionContract("SPY", "SPYC515", expiry, 515, "call", 0.24, 3.2, 3.3, 300, 100),
            OptionContract("SPY", "SPYC520", expiry, 520, "call", 0.20, 2.3, 2.4, 300, 100),
            OptionContract("SPY", "SPYC525", expiry, 525, "call", 0.14, 1.5, 1.6, 300, 100),
            OptionContract("SPY", "SPYC530", expiry, 530, "call", 0.18, 1.8, 1.9, 300, 100),
            OptionContract("SPY", "SPYC535", expiry, 535, "call", 0.10, 0.8, 0.9, 300, 100),
        ]
        candidate = engine.select_credit_spread(snapshot, chain, Regime.BEARISH, 1)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.short_leg.strike, 515)

    def test_risk_engine_respects_symbol_limit(self) -> None:
        db_path = self.temp_root / "test.db"
        settings = make_settings(db_path, max_positions_per_symbol=1)
        repository = Repository(db_path)
        repository.save_position(
            Position(
                position_id="pos-existing",
                symbol="SPY",
                strategy_type=StrategyType.BULL_PUT_CREDIT_SPREAD,
                quantity=1,
                expiration=date.today() + timedelta(days=30),
                short_option_symbol="SPYP445",
                long_option_symbol="SPYP440",
                entry_credit=1.2,
                max_loss=380.0,
            )
        )
        result = RiskEngine(settings, repository).size_position(380.0, "SPY")
        self.assertFalse(result.allowed)
        self.assertIn("max positions reached for symbol", result.reasons)


if __name__ == "__main__":
    unittest.main()
