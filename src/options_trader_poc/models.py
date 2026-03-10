from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import uuid4


UNIVERSE = ("SPY", "QQQ")


class Regime(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class StrategyType(str, Enum):
    BULL_PUT_CREDIT_SPREAD = "bull_put_credit_spread"
    BEAR_CALL_CREDIT_SPREAD = "bear_call_credit_spread"
    TAIL_HEDGE_PUT_SPREAD = "tail_hedge_put_spread"
    NO_TRADE = "no_trade"


class ActionType(str, Enum):
    OPEN = "open"
    HOLD = "hold"
    CLOSE = "close"
    SKIP = "skip"


class PositionStatus(str, Enum):
    PROPOSED = "proposed"
    SUBMITTED = "submitted"
    OPEN = "open"
    CLOSED = "closed"
    REJECTED = "rejected"
    CANCELED = "canceled"


@dataclass(slots=True)
class QuoteSnapshot:
    symbol: str
    price: float
    moving_average_50: float
    realized_volatility: float | None = None
    implied_volatility: float | None = None
    as_of: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class OptionContract:
    symbol: str
    option_symbol: str
    expiration: date
    strike: float
    option_type: str
    delta: float | None
    bid: float
    ask: float
    open_interest: int
    volume: int

    @property
    def mid(self) -> float:
        return round((self.bid + self.ask) / 2, 2)

    @property
    def spread(self) -> float:
        return round(self.ask - self.bid, 2)


@dataclass(slots=True)
class SpreadCandidate:
    symbol: str
    strategy_type: StrategyType
    expiration: date
    short_leg: OptionContract
    long_leg: OptionContract
    width: float
    net_credit: float
    max_loss: float
    quantity: int
    rationale: list[str]
    risk_budget_used: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["strategy_type"] = self.strategy_type.value
        payload["expiration"] = self.expiration.isoformat()
        return payload


@dataclass(slots=True)
class ExitPlan:
    take_profit_pct: float
    stop_loss_multiple: float
    time_exit_dte: int


@dataclass(slots=True)
class Decision:
    decision_id: str
    symbol: str
    regime: Regime
    action: ActionType
    strategy_type: StrategyType
    candidate: SpreadCandidate | None
    max_risk: float
    exit_plan: ExitPlan
    reasons: list[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "regime": self.regime.value,
            "action": self.action.value,
            "strategy_type": self.strategy_type.value,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "max_risk": round(self.max_risk, 2),
            "exit_plan": asdict(self.exit_plan),
            "reasons": self.reasons,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class Position:
    position_id: str
    symbol: str
    strategy_type: StrategyType
    quantity: int
    expiration: date
    short_option_symbol: str
    long_option_symbol: str
    entry_credit: float
    max_loss: float
    status: PositionStatus = PositionStatus.OPEN
    opened_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    current_debit: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["strategy_type"] = self.strategy_type.value
        payload["status"] = self.status.value
        payload["expiration"] = self.expiration.isoformat()
        payload["opened_at"] = self.opened_at.isoformat()
        return payload


@dataclass(slots=True)
class OrderSubmission:
    decision_id: str
    broker_order_id: str | None
    status: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]


@dataclass(slots=True)
class DailySummary:
    trade_date: date
    open_positions: int
    submitted_orders: int
    closed_positions: int
    decisions_logged: int
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "open_positions": self.open_positions,
            "submitted_orders": self.submitted_orders,
            "closed_positions": self.closed_positions,
            "decisions_logged": self.decisions_logged,
            "notes": self.notes,
        }


def new_decision_id() -> str:
    return f"dec-{uuid4().hex[:12]}"


def new_position_id() -> str:
    return f"pos-{uuid4().hex[:12]}"
