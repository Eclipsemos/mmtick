"""Typed application settings loaded from TOML."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class StrategySettings:
    name: str
    bar_minutes: int
    atr_period: int
    atr_multiplier: float
    position_fraction: float


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
    feed: Literal["binance", "yahoo"]
    quantity_step: float
    reference_symbol: str


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
    alpha_warehouse: Path
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
    if execution.fee_bps < 0 or execution.slippage_bps < 0:
        raise ValueError("execution costs cannot be negative")
    if not instruments:
        raise ValueError("at least one instrument is required")

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
        alpha_warehouse=Path(raw["history"]["mastermind_alpha_warehouse"]),
        warmup_bars=int(raw["history"]["warmup_bars"]),
        instruments=instruments,
    )
