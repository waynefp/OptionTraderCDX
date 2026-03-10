from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_ENV_LOADED = False


def load_dotenv_file(path: str = ".env") -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = Path(path)
    if not env_path.exists():
        _ENV_LOADED = True
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
    _ENV_LOADED = True


load_dotenv_file()


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None else default


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    items = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    return items or default


def _get_int_list(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    items = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    return items or default


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path(os.getenv("OPTIONS_TRADER_DB_PATH", "data/options_trader.db"))
    universe_symbols: tuple[str, ...] = _get_list("OPTIONS_TRADER_UNIVERSE", ("SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "XLF"))
    account_size: float = _get_float("OPTIONS_TRADER_ACCOUNT_SIZE", 100_000.0)
    risk_per_trade: float = _get_float("OPTIONS_TRADER_RISK_PER_TRADE", 0.005)
    max_open_risk: float = _get_float("OPTIONS_TRADER_MAX_OPEN_RISK", 0.03)
    max_positions_per_symbol: int = _get_int("OPTIONS_TRADER_MAX_POSITIONS_PER_SYMBOL", 2)
    decisions_per_symbol: int = _get_int("OPTIONS_TRADER_DECISIONS_PER_SYMBOL", 1)
    short_delta_min: float = _get_float("OPTIONS_TRADER_SHORT_DELTA_MIN", 0.15)
    short_delta_max: float = _get_float("OPTIONS_TRADER_SHORT_DELTA_MAX", 0.25)
    spread_widths: tuple[int, ...] = _get_int_list("OPTIONS_TRADER_SPREAD_WIDTHS", (5,))
    take_profit_pct: float = _get_float("OPTIONS_TRADER_EXIT_TAKE_PROFIT", 0.50)
    stop_loss_multiple: float = _get_float("OPTIONS_TRADER_EXIT_STOP_MULTIPLIER", 2.0)
    time_exit_dte: int = _get_int("OPTIONS_TRADER_TIME_EXIT_DTE", 21)
    min_entry_dte: int = _get_int("OPTIONS_TRADER_MIN_ENTRY_DTE", 30)
    max_entry_dte: int = _get_int("OPTIONS_TRADER_MAX_ENTRY_DTE", 45)
    short_put_otm_pct: float = _get_float("OPTIONS_TRADER_SHORT_PUT_OTM_PCT", 0.03)
    short_call_otm_pct: float = _get_float("OPTIONS_TRADER_SHORT_CALL_OTM_PCT", 0.03)
    min_short_otm_pct: float = _get_float("OPTIONS_TRADER_MIN_SHORT_OTM_PCT", 0.015)
    max_short_otm_pct: float = _get_float("OPTIONS_TRADER_MAX_SHORT_OTM_PCT", 0.06)
    min_open_interest: int = _get_int("OPTIONS_TRADER_MIN_OPEN_INTEREST", 100)
    min_volume: int = _get_int("OPTIONS_TRADER_MIN_VOLUME", 25)
    max_spread_pct: float = _get_float("OPTIONS_TRADER_MAX_SPREAD_PCT", 0.12)
    min_net_credit: float = _get_float("OPTIONS_TRADER_MIN_NET_CREDIT", 0.35)
    auto_submit_paper: bool = _get_bool("OPTIONS_TRADER_AUTO_SUBMIT", False)
    tradier_base_url: str = os.getenv("TRADIER_BASE_URL", "https://sandbox.tradier.com/v1")
    tradier_account_id: str = os.getenv("TRADIER_ACCOUNT_ID", "")
    tradier_access_token: str = os.getenv("TRADIER_ACCESS_TOKEN", "")

    @property
    def risk_budget_per_trade(self) -> float:
        return self.account_size * self.risk_per_trade

    @property
    def max_open_risk_dollars(self) -> float:
        return self.account_size * self.max_open_risk

    @property
    def short_target_delta(self) -> float:
        return round((self.short_delta_min + self.short_delta_max) / 2, 4)

    @property
    def spread_width(self) -> int:
        return self.spread_widths[0]
