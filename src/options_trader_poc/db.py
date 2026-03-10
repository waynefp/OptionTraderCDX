from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    regime TEXT NOT NULL,
    action TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    candidate_json TEXT,
    max_risk REAL NOT NULL,
    exit_plan_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    expiration TEXT NOT NULL,
    short_option_symbol TEXT NOT NULL,
    long_option_symbol TEXT NOT NULL,
    entry_credit REAL NOT NULL,
    max_loss REAL NOT NULL,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    current_debit REAL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    broker_order_id TEXT,
    status TEXT NOT NULL,
    request_payload_json TEXT NOT NULL,
    response_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS journal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize(db_path: Path) -> None:
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)
        connection.commit()
