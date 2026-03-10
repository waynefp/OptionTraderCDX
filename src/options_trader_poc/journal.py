from __future__ import annotations

from .models import Decision, OrderSubmission, Position
from .repository import Repository


class Journal:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def log_decision(self, decision: Decision) -> None:
        self.repository.log_decision(decision)
        self.repository.log_event("decision.logged", decision.decision_id, decision.to_dict())

    def log_order(self, submission: OrderSubmission) -> None:
        self.repository.log_order_submission(submission)
        self.repository.log_event("order.submitted", submission.decision_id, {
            "broker_order_id": submission.broker_order_id,
            "status": submission.status,
        })

    def log_position(self, position: Position) -> None:
        self.repository.save_position(position)
        self.repository.log_event("position.saved", position.position_id, position.to_dict())

    def log_exit(self, position: Position, reason: str) -> None:
        self.repository.log_event("position.exit_evaluated", position.position_id, {
            "reason": reason,
            "position": position.to_dict(),
        })
