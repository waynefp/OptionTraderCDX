from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .repository import Repository


@dataclass(slots=True)
class RiskCheckResult:
    allowed: bool
    reasons: list[str]
    quantity: int


class RiskEngine:
    def __init__(self, settings: Settings, repository: Repository) -> None:
        self.settings = settings
        self.repository = repository

    def size_position(
        self,
        max_loss_per_spread: float,
        symbol: str,
        pending_symbol_positions: int = 0,
        pending_open_risk: float = 0.0,
    ) -> RiskCheckResult:
        reasons: list[str] = []
        if max_loss_per_spread <= 0:
            return RiskCheckResult(False, ["invalid max loss per spread"], 0)

        open_positions_for_symbol = self.repository.count_open_positions_for_symbol(symbol) + pending_symbol_positions
        if open_positions_for_symbol >= self.settings.max_positions_per_symbol:
            reasons.append("max positions reached for symbol")

        total_open_risk = self.repository.get_total_open_risk() + pending_open_risk
        if total_open_risk >= self.settings.max_open_risk_dollars:
            reasons.append("max total open risk reached")

        quantity = max(int(self.settings.risk_budget_per_trade // max_loss_per_spread), 0)
        if quantity < 1:
            reasons.append("risk budget too small for one spread")
            return RiskCheckResult(False, reasons, 0)

        projected_risk = total_open_risk + (quantity * max_loss_per_spread)
        if projected_risk > self.settings.max_open_risk_dollars:
            reduced_quantity = int((self.settings.max_open_risk_dollars - total_open_risk) // max_loss_per_spread)
            if reduced_quantity < 1:
                reasons.append("projected open risk exceeds cap")
                return RiskCheckResult(False, reasons, 0)
            quantity = reduced_quantity
            reasons.append("quantity reduced to fit max open risk")

        blocked_reasons = {"max positions reached for symbol", "max total open risk reached", "projected open risk exceeds cap"}
        return RiskCheckResult(not any(reason in blocked_reasons for reason in reasons), reasons, quantity)
