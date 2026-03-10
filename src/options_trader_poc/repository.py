from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import connect, initialize
from .models import Decision, OrderSubmission, Position, PositionStatus, StrategyType


class Repository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        initialize(db_path)

    def log_decision(self, decision: Decision) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO decisions (
                    decision_id, symbol, regime, action, strategy_type, candidate_json,
                    max_risk, exit_plan_json, reasons_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.symbol,
                    decision.regime.value,
                    decision.action.value,
                    decision.strategy_type.value,
                    json.dumps(decision.candidate.to_dict() if decision.candidate else None, default=str),
                    decision.max_risk,
                    json.dumps(asdict(decision.exit_plan), default=str),
                    json.dumps(decision.reasons),
                    decision.created_at.isoformat(),
                ),
            )
            connection.commit()

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_position(self, position: Position) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO positions (
                    position_id, symbol, strategy_type, quantity, expiration,
                    short_option_symbol, long_option_symbol, entry_credit, max_loss,
                    status, opened_at, current_debit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.position_id,
                    position.symbol,
                    position.strategy_type.value,
                    position.quantity,
                    position.expiration.isoformat(),
                    position.short_option_symbol,
                    position.long_option_symbol,
                    position.entry_credit,
                    position.max_loss,
                    position.status.value,
                    position.opened_at.isoformat(),
                    position.current_debit,
                ),
            )
            connection.commit()

    def list_positions(self, statuses: tuple[PositionStatus, ...] | None = None) -> list[Position]:
        with connect(self.db_path) as connection:
            if statuses:
                placeholders = ", ".join("?" for _ in statuses)
                rows = connection.execute(
                    f"SELECT * FROM positions WHERE status IN ({placeholders}) ORDER BY opened_at DESC",
                    tuple(status.value for status in statuses),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM positions ORDER BY opened_at DESC").fetchall()
        return [self._row_to_position(dict(row)) for row in rows]

    def list_open_positions(self) -> list[Position]:
        return self.list_positions((PositionStatus.OPEN, PositionStatus.SUBMITTED))

    def get_total_open_risk(self) -> float:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(max_loss * quantity), 0) AS total_open_risk FROM positions WHERE status IN (?, ?)",
                (PositionStatus.OPEN.value, PositionStatus.SUBMITTED.value),
            ).fetchone()
        return float(row["total_open_risk"])

    def count_open_positions_for_symbol(self, symbol: str) -> int:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT COUNT(1) AS count FROM positions WHERE symbol = ? AND status IN (?, ?)",
                (symbol, PositionStatus.OPEN.value, PositionStatus.SUBMITTED.value),
            ).fetchone()
        return int(row["count"])

    def has_open_position_for_symbol(self, symbol: str) -> bool:
        return self.count_open_positions_for_symbol(symbol) > 0

    def log_order_submission(self, submission: OrderSubmission) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO orders (
                    decision_id, broker_order_id, status, request_payload_json, response_payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    submission.decision_id,
                    submission.broker_order_id,
                    submission.status,
                    json.dumps(submission.request_payload, default=str),
                    json.dumps(submission.response_payload, default=str),
                ),
            )
            connection.commit()

    def list_orders(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, decision_id, broker_order_id, status, request_payload_json, response_payload_json, created_at FROM orders ORDER BY id DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with connect(self.db_path) as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def update_order_status(self, broker_order_id: str, status: str, response_payload: dict[str, Any] | None = None) -> None:
        with connect(self.db_path) as connection:
            if response_payload is None:
                connection.execute(
                    "UPDATE orders SET status = ? WHERE broker_order_id = ?",
                    (status, broker_order_id),
                )
            else:
                connection.execute(
                    "UPDATE orders SET status = ?, response_payload_json = ? WHERE broker_order_id = ?",
                    (status, json.dumps(response_payload, default=str), broker_order_id),
                )
            connection.commit()

    def log_event(self, event_type: str, reference_id: str, payload: dict[str, Any]) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO journal_events (event_type, reference_id, payload_json)
                VALUES (?, ?, ?)
                """,
                (event_type, reference_id, json.dumps(payload, default=str)),
            )
            connection.commit()

    def update_position_status(self, position_id: str, status: PositionStatus, current_debit: float | None = None) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                "UPDATE positions SET status = ?, current_debit = ? WHERE position_id = ?",
                (status.value, current_debit, position_id),
            )
            connection.commit()

    def summary_counts(self, trade_date: date) -> dict[str, int]:
        prefix = trade_date.isoformat()
        with connect(self.db_path) as connection:
            decisions_logged = connection.execute(
                "SELECT COUNT(1) AS count FROM decisions WHERE created_at LIKE ?",
                (f"{prefix}%",),
            ).fetchone()["count"]
            submitted_orders = connection.execute(
                "SELECT COUNT(1) AS count FROM orders WHERE created_at LIKE ?",
                (f"{prefix}%",),
            ).fetchone()["count"]
            open_positions = connection.execute(
                "SELECT COUNT(1) AS count FROM positions WHERE status = ?",
                (PositionStatus.OPEN.value,),
            ).fetchone()["count"]
            closed_positions = connection.execute(
                "SELECT COUNT(1) AS count FROM positions WHERE status = ?",
                (PositionStatus.CLOSED.value,),
            ).fetchone()["count"]
        return {
            "decisions_logged": int(decisions_logged),
            "submitted_orders": int(submitted_orders),
            "open_positions": int(open_positions),
            "closed_positions": int(closed_positions),
        }

    def dashboard_snapshot(self, limit: int = 25) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            decisions = [dict(row) for row in connection.execute(
                "SELECT decision_id, symbol, regime, action, strategy_type, candidate_json, max_risk, reasons_json, created_at FROM decisions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()]
            positions = [dict(row) for row in connection.execute(
                "SELECT position_id, symbol, strategy_type, quantity, expiration, short_option_symbol, long_option_symbol, entry_credit, max_loss, status, opened_at, current_debit FROM positions ORDER BY opened_at DESC LIMIT ?",
                (limit,),
            ).fetchall()]
            orders = [dict(row) for row in connection.execute(
                "SELECT id, decision_id, broker_order_id, status, request_payload_json, response_payload_json, created_at FROM orders ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()]
            events = [dict(row) for row in connection.execute(
                "SELECT id, event_type, reference_id, payload_json, created_at FROM journal_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()]

        return {
            "summary": self.summary_counts(date.today()),
            "decisions": decisions,
            "positions": positions,
            "orders": orders,
            "events": events,
        }

    @staticmethod
    def _row_to_position(row: dict[str, Any]) -> Position:
        return Position(
            position_id=row["position_id"],
            symbol=row["symbol"],
            strategy_type=StrategyType(row["strategy_type"]),
            quantity=int(row["quantity"]),
            expiration=date.fromisoformat(row["expiration"]),
            short_option_symbol=row["short_option_symbol"],
            long_option_symbol=row["long_option_symbol"],
            entry_credit=float(row["entry_credit"]),
            max_loss=float(row["max_loss"]),
            status=PositionStatus(row["status"]),
            opened_at=datetime.fromisoformat(row["opened_at"]),
            current_debit=float(row["current_debit"]) if row["current_debit"] is not None else None,
        )
