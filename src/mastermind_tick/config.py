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


def load_settings(path: str | Path = "config/settings.toml") -> Settings:
    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    project_root = config_path.parent.parent
    app = raw["app"]
    strategy = StrategySettings(**raw["strategy"])
    execution = ExecutionSettings(**raw["execution"])
    instruments = tuple(InstrumentSettings(**value) for value in raw["instruments"])

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
    )
