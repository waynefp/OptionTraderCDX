<<<<<<< HEAD
# Options Trader POC

This repository contains a first-pass implementation of the options trader proof of concept. It keeps the trading logic in a Python service and leaves orchestration, approvals, and notifications to n8n.

## What is included

- FastAPI service entrypoint for scan, automated scan-and-submit, order submission, sync, exit evaluation, and daily summary
- Deterministic strategy logic for SPY and QQQ defined-risk credit spreads
- Live Tradier market-data fetching for quotes, price history, expirations, and option chains
- SQLite-backed journaling for decisions, trades, orders, and fills
- Tradier paper-trading adapter scaffold with injectable transport for tests
- Sample n8n workflow export showing entry scheduling, exit monitoring, and daily summaries
- Unit and integration-style tests for strategy selection, fallback behavior, risk gating, automated submissions, and Tradier request normalization

## Quick start

1. Keep your real credentials in `.env`.
2. Install the project:

```bash
pip install -e .
```

3. Start the API:

```bash
uvicorn options_trader_poc.api:app --reload
```

## Main endpoints

- `GET /health`
- `POST /scan`
- `POST /jobs/scan-and-submit`
- `POST /trades/submit`
- `POST /broker/sync`
- `POST /positions/evaluate-exits`
- `GET /summary/daily`

## URL clarification

- The n8n workflow should call your Python API, typically `http://localhost:8000/...` if both are running on the same machine.
- The Tradier sandbox URL belongs in `.env` as `TRADIER_BASE_URL=https://sandbox.tradier.com/v1`.
- n8n should not call Tradier directly in this design.

## Automation model

- Python owns data fetching, signal generation, risk checks, strike selection, journaling, and order payload creation.
- n8n owns schedules, notifications, and operator summaries.
- Set `OPTIONS_TRADER_AUTO_SUBMIT=true` in `.env` if you want the automated cycle to submit paper trades without human approval.

## Important Tradier sandbox note

Tradier sandbox option data may not always include Greeks. The strategy engine prefers delta-based selection when delta is available and falls back to OTM strike-distance selection when it is not.

## Suggested n8n workflow

1. Morning schedule calls `POST /jobs/scan-and-submit`.
2. Midday and afternoon schedules call `POST /positions/evaluate-exits`.
3. End-of-day schedule calls `GET /summary/daily`.
4. Telegram or email notifications summarize entries, exits, and the daily journal.
=======
# OptionTraderCDX
Option Automation
>>>>>>> origin/main
