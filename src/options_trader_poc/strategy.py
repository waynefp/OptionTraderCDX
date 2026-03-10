from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .config import Settings
from .models import OptionContract, QuoteSnapshot, Regime, SpreadCandidate, StrategyType


def determine_regime(snapshot: QuoteSnapshot) -> Regime:
    threshold = snapshot.moving_average_50
    if snapshot.price > threshold * 1.002:
        return Regime.BULLISH
    if snapshot.price < threshold * 0.998:
        return Regime.BEARISH
    return Regime.NEUTRAL


def _spread_pct(contract: OptionContract) -> float:
    mid = contract.mid
    return (contract.spread / mid) if mid > 0 else 999.0


@dataclass(slots=True)
class StrategyEngine:
    settings: Settings

    def select_credit_spread(
        self,
        snapshot: QuoteSnapshot,
        chain: list[OptionContract],
        regime: Regime,
        quantity: int,
    ) -> SpreadCandidate | None:
        candidates = self.find_credit_spread_candidates(snapshot, chain, regime, quantity, max_candidates=1)
        return candidates[0] if candidates else None

    def find_credit_spread_candidates(
        self,
        snapshot: QuoteSnapshot,
        chain: list[OptionContract],
        regime: Regime,
        quantity: int,
        max_candidates: int,
    ) -> list[SpreadCandidate]:
        strategy = StrategyType.BULL_PUT_CREDIT_SPREAD if regime != Regime.BEARISH else StrategyType.BEAR_CALL_CREDIT_SPREAD
        target_type = "put" if strategy == StrategyType.BULL_PUT_CREDIT_SPREAD else "call"
        target_otm_pct = self.settings.short_put_otm_pct if target_type == "put" else self.settings.short_call_otm_pct

        valid_shorts = [
            contract for contract in chain
            if contract.option_type == target_type and self._is_liquid(contract) and self._is_otm_in_bounds(snapshot, contract)
        ]
        if not valid_shorts:
            return []

        ordered_shorts = sorted(
            valid_shorts,
            key=lambda contract: self._short_rank(snapshot, contract, target_otm_pct),
        )

        candidates: list[SpreadCandidate] = []
        seen: set[tuple[str, str]] = set()
        for short_leg in ordered_shorts:
            for width in self.settings.spread_widths:
                candidate = self._build_candidate(snapshot, chain, strategy, short_leg, width, quantity, target_otm_pct)
                if candidate is None:
                    continue
                key = (candidate.short_leg.option_symbol, candidate.long_leg.option_symbol)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)

        candidates.sort(key=lambda candidate: self._candidate_rank(snapshot, candidate, target_otm_pct))
        return candidates[:max_candidates]

    def build_tail_hedge_recommendation(self, symbol: str, expirations: list[date]) -> dict[str, str] | None:
        if not expirations:
            return None
        return {
            "symbol": symbol,
            "strategy_type": StrategyType.TAIL_HEDGE_PUT_SPREAD.value,
            "recommended_expiration": expirations[-1].isoformat(),
            "note": "budget a small far-OTM put spread manually; not auto-submitted in v1",
        }

    def _build_candidate(
        self,
        snapshot: QuoteSnapshot,
        chain: list[OptionContract],
        strategy: StrategyType,
        short_leg: OptionContract,
        width: int,
        quantity: int,
        target_otm_pct: float,
    ) -> SpreadCandidate | None:
        target_type = short_leg.option_type
        long_strike = short_leg.strike - width if target_type == "put" else short_leg.strike + width
        matching_long_legs = [
            contract for contract in chain
            if contract.option_type == target_type
            and contract.expiration == short_leg.expiration
            and abs(contract.strike - long_strike) < 0.001
            and self._is_long_leg_usable(contract)
        ]
        if not matching_long_legs:
            return None

        long_leg = min(matching_long_legs, key=lambda contract: (_spread_pct(contract), -contract.open_interest, -contract.volume))
        net_credit = round(max(short_leg.mid - long_leg.mid, 0.01), 2)
        if net_credit < self.settings.min_net_credit:
            return None

        realized_width = round(abs(short_leg.strike - long_leg.strike), 2)
        max_loss = round((realized_width - net_credit) * 100, 2)
        otm_pct = self._otm_pct(snapshot, short_leg)
        selection_note = self._selection_note(short_leg)
        rationale = [
            f"price={snapshot.price:.2f}",
            f"ma50={snapshot.moving_average_50:.2f}",
            selection_note,
            f"selected otm_pct={otm_pct:.4f}",
            f"selected width={realized_width:.2f}",
            f"selected expiration={short_leg.expiration.isoformat()}",
            f"net_credit={net_credit:.2f}",
            f"short oi={short_leg.open_interest} vol={short_leg.volume} spread_pct={_spread_pct(short_leg):.4f}",
            f"long oi={long_leg.open_interest} vol={long_leg.volume} spread_pct={_spread_pct(long_leg):.4f}",
        ]
        return SpreadCandidate(
            symbol=snapshot.symbol,
            strategy_type=strategy,
            expiration=short_leg.expiration,
            short_leg=short_leg,
            long_leg=long_leg,
            width=realized_width,
            net_credit=net_credit,
            max_loss=max_loss,
            quantity=quantity,
            rationale=rationale,
            risk_budget_used=round(max_loss * quantity, 2),
        )

    def _short_rank(self, snapshot: QuoteSnapshot, contract: OptionContract, target_otm_pct: float) -> tuple[int, float, float, float, float]:
        delta = abs(contract.delta) if contract.delta is not None else None
        in_band = delta is not None and self.settings.short_delta_min <= delta <= self.settings.short_delta_max
        fallback_penalty = 0 if in_band else 1
        delta_score = abs((delta if delta is not None else self.settings.short_target_delta) - self.settings.short_target_delta)
        return (
            fallback_penalty,
            abs(self._otm_pct(snapshot, contract) - target_otm_pct),
            delta_score,
            _spread_pct(contract),
            -contract.open_interest,
        )

    def _candidate_rank(self, snapshot: QuoteSnapshot, candidate: SpreadCandidate, target_otm_pct: float) -> tuple[float, float, float, float, float, float]:
        short_leg = candidate.short_leg
        delta = abs(short_leg.delta) if short_leg.delta is not None else self.settings.short_target_delta
        dte = (candidate.expiration - date.today()).days
        target_dte = (self.settings.min_entry_dte + self.settings.max_entry_dte) / 2
        credit_ratio = candidate.net_credit / candidate.width if candidate.width else 0.0
        return (
            abs(self._otm_pct(snapshot, short_leg) - target_otm_pct),
            abs(delta - self.settings.short_target_delta),
            abs(dte - target_dte),
            _spread_pct(short_leg) + _spread_pct(candidate.long_leg),
            -credit_ratio,
            -(short_leg.open_interest + candidate.long_leg.open_interest),
        )

    def _selection_note(self, contract: OptionContract) -> str:
        delta = abs(contract.delta) if contract.delta is not None else None
        if delta is not None and self.settings.short_delta_min <= delta <= self.settings.short_delta_max:
            return f"selected short delta={contract.delta:.2f}"
        return "selected fallback strike without greeks"

    def _is_liquid(self, contract: OptionContract) -> bool:
        return (
            contract.bid > 0
            and contract.ask > contract.bid
            and contract.open_interest >= self.settings.min_open_interest
            and contract.volume >= self.settings.min_volume
            and _spread_pct(contract) <= self.settings.max_spread_pct
        )

    def _is_long_leg_usable(self, contract: OptionContract) -> bool:
        return contract.ask > 0 and contract.bid >= 0 and _spread_pct(contract) <= max(self.settings.max_spread_pct, 0.20)

    def _is_otm_in_bounds(self, snapshot: QuoteSnapshot, contract: OptionContract) -> bool:
        otm_pct = self._otm_pct(snapshot, contract)
        return self.settings.min_short_otm_pct <= otm_pct <= self.settings.max_short_otm_pct

    @staticmethod
    def _otm_pct(snapshot: QuoteSnapshot, contract: OptionContract) -> float:
        if contract.option_type == "put":
            return max((snapshot.price - contract.strike) / snapshot.price, 0.0)
        return max((contract.strike - snapshot.price) / snapshot.price, 0.0)
