"""Typed application settings loaded from TOML."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StrategySettings:
    name: str
    bar_minutes: int
    atr_period: int
    atr_multiplier: float
    position_fraction: float
    trend_efficiency_period: int = 8
    minimum_trend_efficiency: float = 0.25
    reversal_confirmation_atr: float = 0.25


@dataclass(frozen=True)
class ExecutionSettings:
    fee_bps: float
    slippage_bps: float
    minimum_notional: float


@dataclass(frozen=True)
class InstrumentSettings:
    id: str
    symbol: str
    display_symbol: str
    name: str
    asset_type: str
    venue: str
    currency: str
    feed: str
    quantity_step: float
    reference_symbol: str
    paper_model: str = "spot"
    leverage: int = 1
    margin_mode: str = "cash"
    position_fraction: float | None = None
    fee_bps: float | None = None
    slippage_bps: float | None = None
    minimum_notional: float | None = None
    market_data_id: str | None = None
    allow_short: bool | None = None

    @property
    def market_id(self) -> str:
        return self.market_data_id or self.id

    @property
    def short_enabled(self) -> bool:
        return self.paper_model == "futures" and self.allow_short is not False


@dataclass(frozen=True)
class LiveSpotSettings:
    enabled: bool = False
    instrument_id: str = "soxlb"
    account_id: str = "soxlb_live"
    database_path: Path = Path("data/live.db")
    api_base_url: str = "https://api.binance.com"
    api_key_env: str = "BINANCE_API_KEY"
    api_secret_env: str = "BINANCE_API_SECRET"
    credentials_path: Path | None = None
    operator_token_path: Path | None = None
    activation_env: str = "MMTICK_LIVE_CONFIRM"
    activation_value: str = "SOXLBUSDT_LIVE"
    allow_order_submission: bool = False
    adopt_existing_position: bool = False
    position_fraction: float = 0.05
    max_order_notional: float = 100.0
    quote_reserve: float = 10.0
    max_slippage_bps: float = 30.0
    max_daily_loss: float = 50.0
    max_orders_per_day: int = 6
    reconcile_seconds: int = 5
    trade_sync_seconds: int = 60
    order_timeout_seconds: int = 30
    recv_window_ms: int = 5000


@dataclass(frozen=True)
class LiveFuturesSettings:
    enabled: bool = False
    instrument_id: str = "soxl_perp"
    account_id: str = "soxl_perp_live"
    database_path: Path = Path("data/live_futures.db")
    api_base_url: str = "https://fapi.binance.com"
    spot_api_base_url: str = "https://api.binance.com"
    api_key_env: str = "BINANCE_API_KEY"
    api_secret_env: str = "BINANCE_API_SECRET"
    credentials_path: Path | None = None
    operator_token_path: Path | None = None
    activation_env: str = "MMTICK_LIVE_CONFIRM"
    activation_value: str = "SOXLUSDT_PERP_LIVE"
    allow_order_submission: bool = False
    adopt_existing_position: bool = False
    leverage: int = 2
    margin_mode: str = "isolated"
    position_mode: str = "hedge"
    position_fraction: float = 0.625
    max_order_notional: float = 100.0
    max_slippage_bps: float = 30.0
    max_daily_loss: float = 50.0
    max_orders_per_day: int = 6
    profit_activation_atr: float = 0.0
    profit_trailing_atr: float = 0.0
    continuation_reentry_atr: float = 0.0
    reconcile_seconds: int = 5
    trade_sync_seconds: int = 60
    order_timeout_seconds: int = 30
    recv_window_ms: int = 5000


@dataclass(frozen=True)
class Settings:
    project_root: Path
    app_name: str
    environment: str
    database_path: Path
    frontend_dist: Path
    initial_cash: float
    equity_snapshot_seconds: int
    strategy: StrategySettings
    execution: ExecutionSettings
    warmup_bars: int
    instruments: tuple[InstrumentSettings, ...]
    live_spot: LiveSpotSettings = LiveSpotSettings()
    live_futures: LiveFuturesSettings = LiveFuturesSettings()


def load_settings(path: str | Path = "config/settings.toml") -> Settings:
    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    project_root = config_path.parent.parent
    app = raw["app"]
    strategy = StrategySettings(**raw["strategy"])
    execution = ExecutionSettings(**raw["execution"])
    instruments = tuple(InstrumentSettings(**value) for value in raw["instruments"])
    live_raw = dict(raw.get("live_spot", {}))
    live_database_path = project_root / live_raw.pop("database_path", "data/live.db")
    credentials_value = live_raw.pop("credentials_path", None)
    credentials_path = project_root / credentials_value if credentials_value else None
    operator_token_value = live_raw.pop("operator_token_path", None)
    operator_token_path = project_root / operator_token_value if operator_token_value else None
    live_spot = LiveSpotSettings(
        database_path=live_database_path,
        credentials_path=credentials_path,
        operator_token_path=operator_token_path,
        **live_raw,
    )
    futures_raw = dict(raw.get("live_futures", {}))
    futures_database_path = project_root / futures_raw.pop("database_path", "data/live_futures.db")
    futures_credentials_value = futures_raw.pop("credentials_path", None)
    futures_credentials_path = (
        project_root / futures_credentials_value if futures_credentials_value else None
    )
    futures_token_value = futures_raw.pop("operator_token_path", None)
    futures_token_path = project_root / futures_token_value if futures_token_value else None
    live_futures = LiveFuturesSettings(
        database_path=futures_database_path,
        credentials_path=futures_credentials_path,
        operator_token_path=futures_token_path,
        **futures_raw,
    )

    if strategy.bar_minutes <= 0 or 60 % strategy.bar_minutes != 0:
        raise ValueError("strategy.bar_minutes must be a positive divisor of 60")
    if strategy.atr_period < 1:
        raise ValueError("strategy.atr_period must be positive")
    if strategy.atr_multiplier <= 0:
        raise ValueError("strategy.atr_multiplier must be positive")
    if not 0 < strategy.position_fraction <= 1:
        raise ValueError("strategy.position_fraction must be in (0, 1]")
    if strategy.trend_efficiency_period < 2:
        raise ValueError("strategy.trend_efficiency_period must be at least 2")
    if not 0 <= strategy.minimum_trend_efficiency <= 1:
        raise ValueError("strategy.minimum_trend_efficiency must be in [0, 1]")
    if strategy.reversal_confirmation_atr < 0:
        raise ValueError("strategy.reversal_confirmation_atr cannot be negative")
    if execution.fee_bps < 0 or execution.slippage_bps < 0:
        raise ValueError("execution costs cannot be negative")
    if not instruments:
        raise ValueError("at least one instrument is required")
    instrument_by_id = {instrument.id: instrument for instrument in instruments}
    if len(instrument_by_id) != len(instruments):
        raise ValueError("instrument ids must be unique")
    for instrument in instruments:
        if instrument.paper_model not in {"spot", "futures"}:
            raise ValueError(f"invalid paper_model for {instrument.id}")
        if instrument.leverage < 1:
            raise ValueError(f"invalid leverage for {instrument.id}")
        if instrument.position_fraction is not None and not 0 < instrument.position_fraction <= 1:
            raise ValueError(f"invalid position_fraction for {instrument.id}")
        if instrument.fee_bps is not None and instrument.fee_bps < 0:
            raise ValueError(f"invalid fee_bps for {instrument.id}")
        if instrument.slippage_bps is not None and instrument.slippage_bps < 0:
            raise ValueError(f"invalid slippage_bps for {instrument.id}")
        market = instrument_by_id.get(instrument.market_id)
        if market is None:
            raise ValueError(f"unknown market_data_id for {instrument.id}: {instrument.market_id}")
        if (market.symbol, market.feed) != (instrument.symbol, instrument.feed):
            raise ValueError(
                f"shared market data must use the same symbol and feed for {instrument.id}"
            )
        if instrument.allow_short and instrument.paper_model != "futures":
            raise ValueError(f"allow_short requires futures paper_model for {instrument.id}")
    live_instrument = instrument_by_id.get(live_spot.instrument_id)
    if live_instrument is None:
        raise ValueError(f"unknown live_spot instrument_id: {live_spot.instrument_id}")
    if live_instrument.paper_model != "spot":
        raise ValueError("live_spot instrument must use the spot paper model")
    if not 0 < live_spot.position_fraction <= 1:
        raise ValueError("live_spot.position_fraction must be in (0, 1]")
    if live_spot.max_order_notional <= 0 or live_spot.quote_reserve < 0:
        raise ValueError("live_spot order limits must be positive")
    if live_spot.max_slippage_bps < 0 or live_spot.max_daily_loss <= 0:
        raise ValueError("live_spot risk limits are invalid")
    if (
        live_spot.max_orders_per_day < 1
        or live_spot.reconcile_seconds < 1
        or live_spot.trade_sync_seconds < live_spot.reconcile_seconds
    ):
        raise ValueError("live_spot frequency limits are invalid")
    if live_spot.order_timeout_seconds < 1 or live_spot.recv_window_ms < 1000:
        raise ValueError("live_spot timing limits are invalid")
    live_futures_instrument = instrument_by_id.get(live_futures.instrument_id)
    if live_futures_instrument is None:
        raise ValueError(f"unknown live_futures instrument_id: {live_futures.instrument_id}")
    if live_futures_instrument.paper_model != "futures":
        raise ValueError("live_futures instrument must use the futures paper model")
    if live_futures.leverage < 1 or live_futures.margin_mode not in {"isolated", "cross"}:
        raise ValueError("live_futures leverage or margin mode is invalid")
    if live_futures.position_mode not in {"hedge"}:
        raise ValueError("live_futures currently requires hedge position mode")
    if not 0 < live_futures.position_fraction <= 1:
        raise ValueError("live_futures.position_fraction must be in (0, 1]")
    if live_futures.max_order_notional < 0 or live_futures.max_daily_loss < 0:
        raise ValueError("live_futures risk limits cannot be negative")
    if live_futures.max_slippage_bps < 0:
        raise ValueError("live_futures slippage limit cannot be negative")
    if live_futures.profit_activation_atr < 0 or live_futures.profit_trailing_atr < 0:
        raise ValueError("live_futures profit protection ATR cannot be negative")
    if (live_futures.profit_activation_atr > 0) != (live_futures.profit_trailing_atr > 0):
        raise ValueError("live_futures profit activation and trailing ATR must both be enabled")
    if live_futures.continuation_reentry_atr < 0:
        raise ValueError("live_futures continuation re-entry ATR cannot be negative")
    if (
        live_futures.max_orders_per_day < 0
        or live_futures.reconcile_seconds < 1
        or live_futures.trade_sync_seconds < live_futures.reconcile_seconds
    ):
        raise ValueError("live_futures frequency limits are invalid")
    if live_futures.order_timeout_seconds < 1 or live_futures.recv_window_ms < 1000:
        raise ValueError("live_futures timing limits are invalid")

    database_path = project_root / app["database_path"]
    frontend_dist = project_root / app["frontend_dist"]
    return Settings(
        project_root=project_root,
        app_name=app["name"],
        environment=app["environment"],
        database_path=database_path,
        frontend_dist=frontend_dist,
        initial_cash=float(app["initial_cash"]),
        equity_snapshot_seconds=int(app["equity_snapshot_seconds"]),
        strategy=strategy,
        execution=execution,
        warmup_bars=int(raw["history"]["warmup_bars"]),
        instruments=instruments,
        live_spot=live_spot,
        live_futures=live_futures,
    )
