from __future__ import annotations

from functools import lru_cache

from .config import Settings
from .repository import Repository
from .service import TradingService
from .tradier import TradierClient


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_repository() -> Repository:
    return Repository(get_settings().db_path)


@lru_cache(maxsize=1)
def get_tradier_client() -> TradierClient:
    return TradierClient(get_settings())


@lru_cache(maxsize=1)
def get_trading_service() -> TradingService:
    return TradingService(get_settings(), get_repository(), get_tradier_client())
