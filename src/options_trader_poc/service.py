from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from .config import Settings
from .journal import Journal
from .models import (
    ActionType,
    DailySummary,
    Decision,
    ExitPlan,
    OptionContract,
    OrderSubmission,
    Position,
    PositionStatus,
    QuoteSnapshot,
    Regime,
    StrategyType,
    new_decision_id,
    new_position_id,
)
from .repository import Repository
from .risk import RiskEngine
from .strategy import StrategyEngine, determine_regime
from .tradier import TradierClient


@dataclass(slots=True)
class ExitEvaluation:
    position_id: str
    action: str
    reason: str
    close_limit_price: float | None
    broker_order_id: str | None = None
    submission_status: str | None = None


class TradingService:
    def __init__(self, settings: Settings, repository: Repository, tradier_client: TradierClient) -> None:
        self.settings = settings
        self.repository = repository
        self.tradier_client = tradier_client
        self.risk_engine = RiskEngine(settings, repository)
        self.strategy_engine = StrategyEngine(settings)
        self.journal = Journal(repository)

    def scan_universe(self, market_data: dict[str, dict[str, Any]], option_chains: dict[str, list[dict[str, Any]]]) -> list[Decision]:
        decisions: list[Decision] = []
        for symbol in self.settings.universe_symbols:
            snapshot_data = market_data.get(symbol)
            chain_data = option_chains.get(symbol, [])
            if not snapshot_data:
                decision = self._build_skip_decision(symbol, ["missing market data"])
                decisions.append(decision)
                self.journal.log_decision(decision)
                continue

            snapshot = QuoteSnapshot(symbol=symbol, **snapshot_data)
            regime = determine_regime(snapshot)
            chain = [self._parse_contract(symbol, row) for row in chain_data]
            symbol_decisions = self._build_trade_decisions(snapshot, chain, regime)
            decisions.extend(symbol_decisions)
            for decision in symbol_decisions:
                self.journal.log_decision(decision)

        primary = self.settings.universe_symbols[0] if self.settings.universe_symbols else "SPY"
        hedge = self.strategy_engine.build_tail_hedge_recommendation(primary, self._available_expirations(option_chains.get(primary, [])))
        if hedge:
            self.repository.log_event("hedge.recommendation", primary, hedge)
        return decisions

    def scan_universe_live(self) -> list[Decision]:
        market_data: dict[str, dict[str, Any]] = {}
        option_chains: dict[str, list[dict[str, Any]]] = {}
        for symbol in self.settings.universe_symbols:
            market_data[symbol] = self.tradier_client.get_market_snapshot(symbol)
            option_chains[symbol] = self.tradier_client.get_option_chain_for_target_dte(symbol)
        return self.scan_universe(market_data, option_chains)

    def run_automated_cycle(self, auto_submit: bool | None = None) -> dict[str, Any]:
        decisions = self.scan_universe_live()
        should_submit = self.settings.auto_submit_paper if auto_submit is None else auto_submit
        submissions: list[dict[str, Any]] = []
        if should_submit:
            for decision in decisions:
                if decision.action == ActionType.OPEN and decision.candidate is not None:
                    submissions.append(self.submit_trade(decision.decision_id))
        return {
            "ran_at": datetime.now(UTC).isoformat(),
            "auto_submit": should_submit,
            "decisions": [decision.to_dict() for decision in decisions],
            "submissions": submissions,
        }

    def submit_trade(self, decision_id: str) -> dict[str, Any]:
        row = self.repository.get_decision(decision_id)
        if not row:
            raise ValueError(f"unknown decision_id: {decision_id}")
        if row["candidate_json"] in ("null", None):
            raise ValueError(f"decision {decision_id} is not a trade candidate")

        candidate_payload = json.loads(row["candidate_json"])
        candidate = self._candidate_from_payload(candidate_payload)
        response_payload = self.tradier_client.submit_multileg_order(candidate)
        broker_order_id = self._extract_broker_order_id(response_payload)
        position = Position(
            position_id=new_position_id(),
            symbol=candidate.symbol,
            strategy_type=candidate.strategy_type,
            quantity=candidate.quantity,
            expiration=candidate.expiration,
            short_option_symbol=candidate.short_leg.option_symbol,
            long_option_symbol=candidate.long_leg.option_symbol,
            entry_credit=candidate.net_credit,
            max_loss=candidate.max_loss,
            status=PositionStatus.SUBMITTED,
        )
        self.journal.log_position(position)

        submission = OrderSubmission(
            decision_id=decision_id,
            broker_order_id=broker_order_id,
            status="submitted",
            request_payload=self.tradier_client.build_multileg_order_payload(candidate),
            response_payload=response_payload,
        )
        self.journal.log_order(submission)
        return {
            "decision_id": decision_id,
            "position_id": position.position_id,
            "broker_order_id": broker_order_id,
            "status": "submitted",
        }

    def sync_orders(self) -> dict[str, Any]:
        local_orders = self.repository.list_orders()
        order_ids = [str(row["broker_order_id"]) for row in local_orders if row.get("broker_order_id")]
        payload = self.tradier_client.sync_orders(order_ids)
        reconciliation = self._reconcile_broker_state(payload, local_orders)
        result = {**payload, "reconciliation": reconciliation}
        self.repository.log_event("broker.sync", datetime.now(UTC).isoformat(), result)
        return result

    def evaluate_exits(self, price_map: dict[str, float] | None = None, auto_submit: bool = True) -> list[ExitEvaluation]:
        evaluations: list[ExitEvaluation] = []
        effective_price_map = dict(price_map or {})
        for position in self.repository.list_open_positions():
            if position.position_id not in effective_price_map:
                effective_price_map[position.position_id] = self.tradier_client.estimate_position_close_debit(position)
            current_debit = effective_price_map.get(position.position_id)
            action = "hold"
            reason = "no exit trigger hit"
            close_limit_price = None
            broker_order_id = None
            submission_status = None

            if current_debit is not None:
                if current_debit <= position.entry_credit * (1 - self.settings.take_profit_pct):
                    action = "close"
                    reason = "take profit reached"
                    close_limit_price = round(current_debit, 2)
                elif current_debit >= position.entry_credit * self.settings.stop_loss_multiple:
                    action = "close"
                    reason = "stop loss reached"
                    close_limit_price = round(current_debit, 2)

            dte = (position.expiration - date.today()).days
            if action == "hold" and dte <= self.settings.time_exit_dte:
                action = "close"
                reason = "time exit reached"
                close_limit_price = round(current_debit or position.entry_credit, 2)

            if action == "close" and close_limit_price is not None and auto_submit:
                response_payload = self.tradier_client.submit_close_order(position, close_limit_price)
                broker_order_id = self._extract_broker_order_id(response_payload)
                submission_status = "submitted"
                self.repository.update_position_status(position.position_id, PositionStatus.CLOSED, current_debit)
                self.journal.log_order(
                    OrderSubmission(
                        decision_id=position.position_id,
                        broker_order_id=broker_order_id,
                        status="close_submitted",
                        request_payload=self.tradier_client.build_close_order_payload(position, close_limit_price),
                        response_payload=response_payload,
                    )
                )
                reason = f"{reason}; close order submitted"
            elif action == "close":
                self.repository.update_position_status(position.position_id, PositionStatus.CLOSED, current_debit)
                submission_status = "not_submitted"

            self.journal.log_exit(position, reason)
            evaluations.append(
                ExitEvaluation(
                    position_id=position.position_id,
                    action=action,
                    reason=reason,
                    close_limit_price=close_limit_price,
                    broker_order_id=broker_order_id,
                    submission_status=submission_status,
                )
            )
        return evaluations

    def daily_summary(self, trade_date: date | None = None) -> DailySummary:
        effective_date = trade_date or date.today()
        counts = self.repository.summary_counts(effective_date)
        notes = [
            "paper trading only",
            "tail hedge remains recommendation-only in v1",
        ]
        return DailySummary(trade_date=effective_date, notes=notes, **counts)

    def _build_trade_decisions(self, snapshot: QuoteSnapshot, chain: list[OptionContract], regime: Regime) -> list[Decision]:
        exit_plan = ExitPlan(
            take_profit_pct=self.settings.take_profit_pct,
            stop_loss_multiple=self.settings.stop_loss_multiple,
            time_exit_dte=self.settings.time_exit_dte,
        )
        if not chain:
            return [
                Decision(
                    decision_id=new_decision_id(),
                    symbol=snapshot.symbol,
                    regime=regime,
                    action=ActionType.SKIP,
                    strategy_type=StrategyType.NO_TRADE,
                    candidate=None,
                    max_risk=0.0,
                    exit_plan=exit_plan,
                    reasons=["missing option chain"],
                )
            ]

        probe_candidates = self.strategy_engine.find_credit_spread_candidates(
            snapshot,
            chain,
            regime,
            quantity=1,
            max_candidates=max(self.settings.decisions_per_symbol * 4, 4),
        )
        if not probe_candidates:
            return [
                Decision(
                    decision_id=new_decision_id(),
                    symbol=snapshot.symbol,
                    regime=regime,
                    action=ActionType.SKIP,
                    strategy_type=StrategyType.NO_TRADE,
                    candidate=None,
                    max_risk=0.0,
                    exit_plan=exit_plan,
                    reasons=["no liquid spread candidate in delta band or OTM fallback"],
                )
            ]

        decisions: list[Decision] = []
        pending_symbol_positions = 0
        pending_open_risk = 0.0
        rejected_reasons: list[str] = []

        for probe_candidate in probe_candidates:
            risk_check = self.risk_engine.size_position(
                probe_candidate.max_loss,
                snapshot.symbol,
                pending_symbol_positions=pending_symbol_positions,
                pending_open_risk=pending_open_risk,
            )
            reasons = list(probe_candidate.rationale) + risk_check.reasons
            if not risk_check.allowed:
                rejected_reasons.append("; ".join(reasons))
                continue

            candidate = self._candidate_with_quantity(probe_candidate, risk_check.quantity)
            reasons.append("risk limits satisfied")
            decisions.append(
                Decision(
                    decision_id=new_decision_id(),
                    symbol=snapshot.symbol,
                    regime=regime,
                    action=ActionType.OPEN,
                    strategy_type=candidate.strategy_type,
                    candidate=candidate,
                    max_risk=candidate.max_loss * candidate.quantity,
                    exit_plan=exit_plan,
                    reasons=reasons,
                )
            )
            pending_symbol_positions += 1
            pending_open_risk += candidate.max_loss * candidate.quantity
            if len(decisions) >= self.settings.decisions_per_symbol:
                break

        if decisions:
            return decisions

        return [
            Decision(
                decision_id=new_decision_id(),
                symbol=snapshot.symbol,
                regime=regime,
                action=ActionType.SKIP,
                strategy_type=StrategyType.NO_TRADE,
                candidate=None,
                max_risk=0.0,
                exit_plan=exit_plan,
                reasons=rejected_reasons[:3] or ["risk limits blocked valid candidates"],
            )
        ]

    def _build_skip_decision(self, symbol: str, reasons: list[str]) -> Decision:
        return Decision(
            decision_id=new_decision_id(),
            symbol=symbol,
            regime=Regime.NEUTRAL,
            action=ActionType.SKIP,
            strategy_type=StrategyType.NO_TRADE,
            candidate=None,
            max_risk=0.0,
            exit_plan=ExitPlan(
                take_profit_pct=self.settings.take_profit_pct,
                stop_loss_multiple=self.settings.stop_loss_multiple,
                time_exit_dte=self.settings.time_exit_dte,
            ),
            reasons=reasons,
        )

    @staticmethod
    def _parse_contract(symbol: str, payload: dict[str, Any]) -> OptionContract:
        delta_value = payload.get("delta")
        return OptionContract(
            symbol=symbol,
            option_symbol=payload["option_symbol"],
            expiration=date.fromisoformat(payload["expiration"]),
            strike=float(payload["strike"]),
            option_type=payload["option_type"],
            delta=float(delta_value) if delta_value not in (None, "") else None,
            bid=float(payload["bid"]),
            ask=float(payload["ask"]),
            open_interest=int(payload.get("open_interest", 0)),
            volume=int(payload.get("volume", 0)),
        )

    @staticmethod
    def _available_expirations(chain_data: list[dict[str, Any]]) -> list[date]:
        return sorted({date.fromisoformat(item["expiration"]) for item in chain_data}) if chain_data else []

    def _candidate_from_payload(self, payload: dict[str, Any]):
        short_leg = self._parse_contract(payload["symbol"], payload["short_leg"])
        long_leg = self._parse_contract(payload["symbol"], payload["long_leg"])
        from .models import SpreadCandidate

        return SpreadCandidate(
            symbol=payload["symbol"],
            strategy_type=StrategyType(payload["strategy_type"]),
            expiration=date.fromisoformat(payload["expiration"]),
            short_leg=short_leg,
            long_leg=long_leg,
            width=float(payload["width"]),
            net_credit=float(payload["net_credit"]),
            max_loss=float(payload["max_loss"]),
            quantity=int(payload["quantity"]),
            rationale=list(payload["rationale"]),
            risk_budget_used=float(payload["risk_budget_used"]),
        )

    def _candidate_with_quantity(self, candidate, quantity: int):
        return replace(candidate, quantity=quantity, risk_budget_used=round(candidate.max_loss * quantity, 2))

    def _reconcile_broker_state(self, payload: dict[str, Any], local_orders: list[dict[str, Any]]) -> dict[str, Any]:
        order_lookup = {
            item["id"]: item
            for item in [*payload.get("orders", []), *payload.get("order_details", [])]
            if item.get("id")
        }
        live_option_symbols = {
            item["option_symbol"]
            for item in payload.get("positions", [])
            if item.get("option_symbol")
        }

        order_updates = 0
        position_updates: list[dict[str, str]] = []

        for row in local_orders:
            broker_order_id = row.get("broker_order_id")
            if not broker_order_id:
                continue
            broker_order = order_lookup.get(str(broker_order_id))
            if not broker_order:
                continue
            broker_status = broker_order.get("status", "unknown")
            if row.get("status") != broker_status:
                self.repository.update_order_status(str(broker_order_id), broker_status, broker_order.get("raw"))
                order_updates += 1

        active_positions = self.repository.list_positions((PositionStatus.SUBMITTED, PositionStatus.OPEN))
        for position in active_positions:
            matching_order = self._latest_order_for_position(position, local_orders)
            next_status: PositionStatus | None = None
            reason = ""
            if {position.short_option_symbol, position.long_option_symbol}.issubset(live_option_symbols):
                next_status = PositionStatus.OPEN
                reason = "broker positions include both legs"
            elif matching_order:
                broker_order = order_lookup.get(str(matching_order.get("broker_order_id")))
                broker_status = broker_order.get("status") if broker_order else None
                is_close_order = self._is_close_order(matching_order)
                if broker_status in {"canceled", "expired"}:
                    next_status = PositionStatus.OPEN if is_close_order else PositionStatus.CANCELED
                    reason = "broker order canceled"
                elif broker_status in {"rejected", "error", "failed"}:
                    next_status = PositionStatus.REJECTED
                    reason = "broker order rejected"
                elif broker_status == "filled" and is_close_order:
                    next_status = PositionStatus.CLOSED
                    reason = "broker close order filled"
                elif broker_status == "filled" and position.status == PositionStatus.SUBMITTED:
                    next_status = PositionStatus.OPEN
                    reason = "broker entry order filled"

            if next_status and next_status != position.status:
                self.repository.update_position_status(position.position_id, next_status, position.current_debit)
                position_updates.append(
                    {
                        "position_id": position.position_id,
                        "from": position.status.value,
                        "to": next_status.value,
                        "reason": reason,
                    }
                )

        return {
            "order_updates": order_updates,
            "position_updates": position_updates,
            "live_option_symbols": sorted(live_option_symbols),
        }

    @staticmethod
    def _extract_broker_order_id(payload: dict[str, Any]) -> str | None:
        order = payload.get("order") if isinstance(payload, dict) else None
        if isinstance(order, dict):
            return str(order.get("id")) if order.get("id") is not None else None
        return None

    @staticmethod
    def _order_payload(row: dict[str, Any]) -> dict[str, Any]:
        raw = row.get("request_payload_json")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _latest_order_for_position(self, position: Position, local_orders: list[dict[str, Any]]) -> dict[str, Any] | None:
        matches = []
        for row in local_orders:
            payload = self._order_payload(row)
            leg_symbols = {payload.get("option_symbol[0]"), payload.get("option_symbol[1]")}
            if {position.short_option_symbol, position.long_option_symbol} == leg_symbols:
                matches.append(row)
        return matches[0] if matches else None

    def _is_close_order(self, row: dict[str, Any]) -> bool:
        payload = self._order_payload(row)
        return str(payload.get("side[0]", "")).startswith("buy_to_close")
