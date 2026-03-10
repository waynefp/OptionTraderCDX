from __future__ import annotations

import html
import json
from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .app import get_repository, get_trading_service


app = FastAPI(title="Options Trader POC", version="0.3.0")


class ScanRequest(BaseModel):
    market_data: dict[str, dict[str, Any]] = Field(default_factory=dict)
    option_chains: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    use_live_data: bool = True


class AutomatedRunRequest(BaseModel):
    auto_submit: bool | None = None


class SubmitTradeRequest(BaseModel):
    decision_id: str


class ExitEvaluationRequest(BaseModel):
    price_map: dict[str, float] = Field(default_factory=dict)
    use_live_data: bool = True
    auto_submit: bool = True


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/dashboard/data")
def dashboard_data(limit: int = 25) -> dict[str, Any]:
    return get_repository().dashboard_snapshot(limit)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(limit: int = 25) -> str:
    snapshot = get_repository().dashboard_snapshot(limit)
    summary = snapshot["summary"]

    def format_json(value: str | None) -> str:
        if not value:
            return ""
        try:
            parsed = json.loads(value)
            return html.escape(json.dumps(parsed, indent=2))
        except json.JSONDecodeError:
            return html.escape(value)

    def render_rows(rows: list[dict[str, Any]], columns: list[str], json_columns: set[str] | None = None) -> str:
        json_columns = json_columns or set()
        rendered = []
        for row in rows:
            cells = []
            for column in columns:
                value = row.get(column)
                if column in json_columns:
                    content = f"<pre>{format_json(value)}</pre>"
                else:
                    content = html.escape("" if value is None else str(value))
                cells.append(f"<td>{content}</td>")
            rendered.append(f"<tr>{''.join(cells)}</tr>")
        return "".join(rendered) or f"<tr><td colspan='{len(columns)}'>No rows</td></tr>"

    return f"""
    <html>
      <head>
        <title>Options Trader Dashboard</title>
        <style>
          body {{ font-family: Georgia, serif; background: #f7f4ed; color: #1b1a17; margin: 0; padding: 24px; }}
          h1, h2 {{ margin: 0 0 12px 0; }}
          .summary {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 20px 0 28px; }}
          .card {{ background: #fffdf8; border: 1px solid #d9d0bf; border-radius: 12px; padding: 14px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
          .value {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
          table {{ width: 100%; border-collapse: collapse; margin-bottom: 28px; background: #fffdf8; }}
          th, td {{ border: 1px solid #d9d0bf; padding: 8px; text-align: left; vertical-align: top; font-size: 13px; }}
          th {{ background: #efe7d5; position: sticky; top: 0; }}
          pre {{ margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 12px; }}
          .note {{ color: #5b5244; margin-bottom: 20px; }}
        </style>
      </head>
      <body>
        <h1>Options Trader Dashboard</h1>
        <p class="note">Use this to inspect what the system decided, what it submitted, and what it thinks is open. For API output, hit <code>/dashboard/data</code>.</p>
        <div class="summary">
          <div class="card"><div>Decisions Today</div><div class="value">{summary['decisions_logged']}</div></div>
          <div class="card"><div>Orders Today</div><div class="value">{summary['submitted_orders']}</div></div>
          <div class="card"><div>Open Positions</div><div class="value">{summary['open_positions']}</div></div>
          <div class="card"><div>Closed Positions</div><div class="value">{summary['closed_positions']}</div></div>
        </div>

        <h2>Recent Decisions</h2>
        <table>
          <thead><tr><th>decision_id</th><th>symbol</th><th>regime</th><th>action</th><th>strategy_type</th><th>max_risk</th><th>created_at</th><th>candidate_json</th><th>reasons_json</th></tr></thead>
          <tbody>{render_rows(snapshot['decisions'], ['decision_id','symbol','regime','action','strategy_type','max_risk','created_at','candidate_json','reasons_json'], {'candidate_json','reasons_json'})}</tbody>
        </table>

        <h2>Recent Positions</h2>
        <table>
          <thead><tr><th>position_id</th><th>symbol</th><th>status</th><th>strategy_type</th><th>quantity</th><th>expiration</th><th>short_option_symbol</th><th>long_option_symbol</th><th>entry_credit</th><th>current_debit</th><th>opened_at</th></tr></thead>
          <tbody>{render_rows(snapshot['positions'], ['position_id','symbol','status','strategy_type','quantity','expiration','short_option_symbol','long_option_symbol','entry_credit','current_debit','opened_at'])}</tbody>
        </table>

        <h2>Recent Orders</h2>
        <table>
          <thead><tr><th>id</th><th>reference</th><th>broker_order_id</th><th>status</th><th>created_at</th><th>request_payload_json</th><th>response_payload_json</th></tr></thead>
          <tbody>{render_rows(snapshot['orders'], ['id','decision_id','broker_order_id','status','created_at','request_payload_json','response_payload_json'], {'request_payload_json','response_payload_json'})}</tbody>
        </table>

        <h2>Recent Journal Events</h2>
        <table>
          <thead><tr><th>id</th><th>event_type</th><th>reference_id</th><th>created_at</th><th>payload_json</th></tr></thead>
          <tbody>{render_rows(snapshot['events'], ['id','event_type','reference_id','created_at','payload_json'], {'payload_json'})}</tbody>
        </table>
      </body>
    </html>
    """


@app.post("/scan")
def scan(request: ScanRequest) -> dict[str, Any]:
    service = get_trading_service()
    try:
        if request.use_live_data and not request.market_data and not request.option_chains:
            decisions = service.scan_universe_live()
        else:
            decisions = service.scan_universe(request.market_data, request.option_chains)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"decisions": [decision.to_dict() for decision in decisions]}


@app.post("/jobs/scan-and-submit")
def scan_and_submit(request: AutomatedRunRequest) -> dict[str, Any]:
    try:
        return get_trading_service().run_automated_cycle(request.auto_submit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/trades/submit")
def submit_trade(request: SubmitTradeRequest) -> dict[str, Any]:
    try:
        return get_trading_service().submit_trade(request.decision_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/broker/sync")
def sync_orders() -> dict[str, Any]:
    return get_trading_service().sync_orders()


@app.post("/positions/evaluate-exits")
def evaluate_exits(request: ExitEvaluationRequest) -> dict[str, Any]:
    evaluations = get_trading_service().evaluate_exits(
        request.price_map if request.price_map or not request.use_live_data else None,
        auto_submit=request.auto_submit,
    )
    return {
        "evaluations": [
            {
                "position_id": item.position_id,
                "action": item.action,
                "reason": item.reason,
                "close_limit_price": item.close_limit_price,
                "broker_order_id": item.broker_order_id,
                "submission_status": item.submission_status,
            }
            for item in evaluations
        ]
    }


@app.get("/summary/daily")
def daily_summary(trade_date: date | None = None) -> dict[str, Any]:
    return get_trading_service().daily_summary(trade_date).to_dict()
