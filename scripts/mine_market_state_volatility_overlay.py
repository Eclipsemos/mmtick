#!/usr/bin/env python3
"""Search a causal market-state overlay with a causal volatility target."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_factor_portfolio import CONFIRMATION, DISCOVERY, VALIDATION
from mine_market_state_overlay import (
    ASSETS,
    BASE_OVERLAY_TURNOVER_BPS,
    BASELINES,
    DEVELOPMENT_YEARS,
    STRESS_OVERLAY_TURNOVER_BPS,
    MarketStateSignal,
    _baseline_results,
    _daily_signals,
    _target_month_rate,
)
from train_walk_forward_factor import _anchor_context

from mastermind_tick.bar_research import aggregate_bars, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_overlay import (
    SignalOverlayConfig,
    VolatilityTargetConfig,
    evaluate_signal_overlay,
    evaluate_signal_volatility_overlay,
)
from mastermind_tick.factor_portfolio import DailyReturns, PortfolioResult
from mastermind_tick.market_metrics import (
    METRIC_FEATURES,
    causal_metric_features,
    load_metric_archives,
    metric_targets,
)

TARGET_MONTHLY_RETURN = Decimal("0.15")
MIN_TARGET_RATE = Decimal("0.15")
CONFIRMATION_TARGET_RATE = Decimal("0.5")
STATE_SHORTLIST_SIZE = 300


@dataclass(frozen=True)
class VolatilityCandidate:
    lookback_days: int
    target_daily_volatility: Decimal
    minimum_exposure: Decimal
    maximum_exposure: Decimal
    rebalance_frequency: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "lookback_days": self.lookback_days,
            "target_daily_volatility": float(self.target_daily_volatility),
            "minimum_exposure": float(self.minimum_exposure),
            "maximum_exposure": float(self.maximum_exposure),
            "rebalance_frequency": self.rebalance_frequency,
        }

    def config(self, turnover_bps: Decimal) -> VolatilityTargetConfig:
        return VolatilityTargetConfig(
            self.lookback_days,
            self.target_daily_volatility,
            self.minimum_exposure,
            self.maximum_exposure,
            self.rebalance_frequency,
            turnover_bps,
        )


@dataclass(frozen=True)
class StateCandidate:
    baseline: str
    signal: MarketStateSignal
    config: SignalOverlayConfig

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "signal": self.signal.as_dict(),
            "config": self.config.as_dict(),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/market_state_volatility/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading BTC/ETH history and causal market metrics", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    bars = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
    anchor = _anchor_context(bars, loaded)
    metric_bars = {
        asset: load_metric_archives(args.metrics_dir, symbol) for asset, symbol in ASSETS.items()
    }
    features = {
        asset: {
            window: causal_metric_features(
                bars[asset], metric_bars[asset], normalization_window=window
            )
            for window in (180, 540, 1080)
        }
        for asset in ASSETS
    }
    crowding_targets = metric_targets(
        features["eth_perp"][180]["top_position_crowding"],
        threshold=Decimal("2"),
        polarity="fade",
        direction="long_only",
    )
    development_period = (DISCOVERY[0], VALIDATION[1])
    periods = {
        "discovery": DISCOVERY,
        "validation": VALIDATION,
        **DEVELOPMENT_YEARS,
    }
    development_baselines = _baseline_results(
        anchor, bars, funding, crowding_targets, development_period, stress=False
    )
    split_baselines = {
        name: _baseline_results(anchor, bars, funding, crowding_targets, period, stress=False)
        for name, period in periods.items()
    }
    signals = tuple(
        MarketStateSignal(asset, window, feature)
        for asset in ASSETS
        for window in (180, 540, 1080)
        for feature in METRIC_FEATURES
    )
    state_configs = _state_configs()
    state_candidates = _rank_state_candidates(
        signals,
        state_configs,
        features,
        bars,
        split_baselines,
    )
    state_candidates = state_candidates[:STATE_SHORTLIST_SIZE]
    print(f"retained {len(state_candidates)} state candidates", flush=True)

    volatility_candidates = _volatility_candidates()
    rows = []
    for index, state in enumerate(state_candidates, start=1):
        development_raw = _evaluate_state(
            state,
            features,
            bars,
            development_baselines,
            BASE_OVERLAY_TURNOVER_BPS,
        )
        for volatility in volatility_candidates:
            development = {
                name: _evaluate_combined_period(
                    state,
                    features,
                    bars,
                    development_baselines,
                    development_raw.daily_returns,
                    volatility,
                    period,
                    BASE_OVERLAY_TURNOVER_BPS,
                )
                for name, period in periods.items()
            }
            if _development_eligible(development):
                rows.append(
                    {
                        "state": state,
                        "volatility": volatility,
                        "development": development,
                        "score": _score(development),
                    }
                )
        if index % 10 == 0:
            print(f"risk search {index}/{len(state_candidates)}; eligible={len(rows)}", flush=True)
    ranked = sorted(rows, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None

    confirmation = None
    stress = None
    diagnostics = []
    if selected:
        confirmation_baselines = _baseline_results(
            anchor, bars, funding, crowding_targets, CONFIRMATION, stress=False
        )
        stress_baselines = _baseline_results(
            anchor, bars, funding, crowding_targets, CONFIRMATION, stress=True
        )
        confirmation_raw = _evaluate_state(
            selected["state"],
            features,
            bars,
            confirmation_baselines,
            BASE_OVERLAY_TURNOVER_BPS,
        )
        confirmation = _evaluate_combined_period(
            selected["state"],
            features,
            bars,
            confirmation_baselines,
            confirmation_raw.daily_returns,
            selected["volatility"],
            None,
            BASE_OVERLAY_TURNOVER_BPS,
        )
        stress = _evaluate_combined_period(
            selected["state"],
            features,
            bars,
            stress_baselines,
            confirmation_raw.daily_returns,
            selected["volatility"],
            None,
            STRESS_OVERLAY_TURNOVER_BPS,
        )
        for row in ranked[:20]:
            row_confirmation_raw = _evaluate_state(
                row["state"],
                features,
                bars,
                confirmation_baselines,
                BASE_OVERLAY_TURNOVER_BPS,
            )
            row_confirmation = _evaluate_combined_period(
                row["state"],
                features,
                bars,
                confirmation_baselines,
                row_confirmation_raw.daily_returns,
                row["volatility"],
                None,
                BASE_OVERLAY_TURNOVER_BPS,
            )
            row_stress = _evaluate_combined_period(
                row["state"],
                features,
                bars,
                stress_baselines,
                row_confirmation_raw.daily_returns,
                row["volatility"],
                None,
                STRESS_OVERLAY_TURNOVER_BPS,
            )
            diagnostics.append(
                {
                    "state": row["state"].as_dict(),
                    "volatility": row["volatility"].as_dict(),
                    "base": _public_result(row_confirmation),
                    "stress": _public_result(row_stress),
                    "meets_confirmation_gates": _confirmation_eligible(
                        row_confirmation, row_stress
                    ),
                }
            )

    payload = _report(
        bars,
        metric_bars,
        len(BASELINES) * len(signals) * len(state_configs),
        len(state_candidates),
        len(volatility_candidates),
        ranked,
        selected,
        confirmation,
        stress,
        diagnostics,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"market-state-volatility-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _state_configs() -> tuple[SignalOverlayConfig, ...]:
    return tuple(
        SignalOverlayConfig(
            threshold,
            low,
            high,
            "below",
            turnover_bps=BASE_OVERLAY_TURNOVER_BPS,
        )
        for threshold in map(Decimal, ("0.75", "1", "1.25"))
        for low in map(Decimal, ("0.5", "0.6", "0.7", "0.75", "0.8"))
        for high in map(Decimal, ("1.6", "1.8", "2"))
    )


def _volatility_candidates() -> tuple[VolatilityCandidate, ...]:
    return tuple(
        VolatilityCandidate(lookback, target, minimum, maximum, frequency)
        for lookback in (20, 40)
        for target in map(Decimal, ("0.02", "0.025", "0.03"))
        for minimum in map(Decimal, ("0.5", "0.6", "0.7"))
        for maximum in map(Decimal, ("0.9", "1.1"))
        for frequency in ("daily",)
        if minimum < maximum
    )


def _rank_state_candidates(
    signals: tuple[MarketStateSignal, ...],
    configs: tuple[SignalOverlayConfig, ...],
    features: dict[str, dict[int, dict[str, tuple[Decimal | None, ...]]]],
    bars: dict[str, list[Any]],
    split_baselines: dict[str, dict[str, PortfolioResult]],
) -> list[StateCandidate]:
    rows = []
    for baseline in BASELINES:
        for signal in signals:
            values = features[signal.asset][signal.window][signal.feature]
            split_signals = {
                name: _daily_signals(
                    bars[signal.asset], values, split_baselines[name][baseline].daily_returns
                )
                for name in split_baselines
            }
            for config in configs:
                state = StateCandidate(baseline, signal, config)
                split_results = {
                    name: evaluate_signal_overlay(
                        split_baselines[name][baseline].daily_returns,
                        split_signals[name],
                        config,
                    )
                    for name in split_baselines
                }
                all_results = list(split_results.values())
                if not _relaxed_state_eligible(all_results):
                    continue
                rows.append(
                    {
                        "state": state,
                        "score": _state_score(split_results),
                    }
                )
    return [row["state"] for row in sorted(rows, key=lambda row: row["score"], reverse=True)]


def _evaluate_state(
    state: StateCandidate,
    features: dict[str, dict[int, dict[str, tuple[Decimal | None, ...]]]],
    bars: dict[str, list[Any]],
    baselines: dict[str, PortfolioResult],
    turnover_bps: Decimal,
) -> PortfolioResult:
    values = features[state.signal.asset][state.signal.window][state.signal.feature]
    signals = _daily_signals(
        bars[state.signal.asset], values, baselines[state.baseline].daily_returns
    )
    config = SignalOverlayConfig(
        state.config.threshold,
        state.config.low_exposure,
        state.config.high_exposure,
        state.config.mode,
        turnover_bps=turnover_bps,
    )
    return evaluate_signal_overlay(baselines[state.baseline].daily_returns, signals, config)


def _evaluate_combined_period(
    state: StateCandidate,
    features: dict[str, dict[int, dict[str, tuple[Decimal | None, ...]]]],
    bars: dict[str, list[Any]],
    baselines: dict[str, PortfolioResult],
    volatility_signal_returns: DailyReturns,
    candidate: VolatilityCandidate,
    period: tuple[int, int] | None,
    turnover_bps: Decimal,
) -> PortfolioResult:
    values = features[state.signal.asset][state.signal.window][state.signal.feature]
    signals = _daily_signals(
        bars[state.signal.asset], values, baselines[state.baseline].daily_returns
    )
    signal_config = SignalOverlayConfig(
        state.config.threshold,
        state.config.low_exposure,
        state.config.high_exposure,
        state.config.mode,
        turnover_bps=turnover_bps,
    )
    return evaluate_signal_volatility_overlay(
        baselines[state.baseline].daily_returns,
        signals,
        signal_config,
        candidate.config(turnover_bps),
        volatility_signal_returns=volatility_signal_returns,
        start=_date_from_ms(period[0]) if period else None,
        end=_date_from_ms(period[1]) if period else None,
    )


def _relaxed_state_eligible(results: list[PortfolioResult]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.7")
        and result.positive_month_rate >= Decimal("0.5")
        and _target_month_rate(result) >= MIN_TARGET_RATE
        and not result.bankrupt
        for result in results
    )


def _state_score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    annual = [results[year] for year in DEVELOPMENT_YEARS]
    return (
        min(_target_month_rate(result) for result in annual),
        sum((_target_month_rate(result) for result in annual), Decimal("0")),
        min(result.positive_month_rate for result in annual),
        min(result.net_return for result in annual),
        min(result.max_drawdown for result in annual),
    )


def _development_eligible(results: dict[str, PortfolioResult]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and _target_month_rate(result) >= MIN_TARGET_RATE
        and not result.bankrupt
        for result in results.values()
    )


def _score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    annual = [results[year] for year in DEVELOPMENT_YEARS]
    return (
        min(_target_month_rate(result) for result in annual),
        sum((_target_month_rate(result) for result in annual), Decimal("0")),
        min(result.positive_month_rate for result in annual),
        min(result.net_return for result in annual),
        min(result.worst_month for result in results.values()),
        min(result.max_drawdown for result in results.values()),
    )


def _confirmation_eligible(base: PortfolioResult, stress: PortfolioResult) -> bool:
    return bool(
        _target_month_rate(base) >= CONFIRMATION_TARGET_RATE
        and _target_month_rate(stress) >= CONFIRMATION_TARGET_RATE
        and base.net_return > 0
        and stress.net_return > 0
        and base.max_drawdown >= Decimal("-0.35")
        and stress.max_drawdown >= Decimal("-0.35")
    )


def _public_result(result: PortfolioResult, *, include_daily: bool = False) -> dict[str, Any]:
    payload = result.as_dict(include_daily=include_daily)
    payload["target_15pct_month_rate"] = float(_target_month_rate(result))
    return payload


def _report(
    bars: dict[str, list[Any]],
    metric_bars: dict[str, dict[int, Any]],
    state_count: int,
    state_shortlist_count: int,
    volatility_count: int,
    ranked: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    confirmation: PortfolioResult | None,
    stress: PortfolioResult | None,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    achieved = bool(confirmation and stress and _confirmation_eligible(confirmation, stress))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "causal market-state overlay with causal volatility target",
        "data": {
            asset: {
                "price_first": _timestamp(bars[asset][0].start_ms),
                "price_last": _timestamp(bars[asset][-1].end_ms),
                "metric_first": _timestamp(min(metric_bars[asset])),
                "metric_last": _timestamp(max(metric_bars[asset]) + 14_400_000 - 1),
            }
            for asset in ASSETS
        },
        "search": {
            "state_candidate_count": state_count,
            "state_shortlist_count": state_shortlist_count,
            "volatility_candidate_count": volatility_count,
            "development_selection_only": True,
            "search_protocol_frozen_before_confirmation": False,
            "development_monthly_target": float(TARGET_MONTHLY_RETURN),
        },
        "selection": {
            "eligible_count": len(ranked),
            "selected": (
                {
                    "state": selected["state"].as_dict(),
                    "volatility": selected["volatility"].as_dict(),
                    "score": [float(value) for value in selected["score"]],
                    "development": {
                        name: _public_result(result)
                        for name, result in selected["development"].items()
                    },
                }
                if selected
                else None
            ),
            "top_development_configurations": [
                {
                    "state": row["state"].as_dict(),
                    "volatility": row["volatility"].as_dict(),
                    "score": [float(value) for value in row["score"]],
                }
                for row in ranked[:20]
            ],
        },
        "confirmation": _public_result(confirmation, include_daily=True) if confirmation else None,
        "stress_confirmation": _public_result(stress) if stress else None,
        "confirmation_neighborhood_diagnostic": {
            "used_for_selection": False,
            "configuration_count": len(diagnostics),
            "meeting_gate_count": sum(row["meets_confirmation_gates"] for row in diagnostics),
            "configurations": diagnostics,
        },
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "minimum_confirmation_target_month_rate": float(CONFIRMATION_TARGET_RATE),
            "achieved": achieved,
        },
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The development-selected overlay met reused base and stress confirmation gates; "
                "fresh forward evidence remains required."
                if achieved
                else "The development-selected overlay failed base or stress confirmation gates."
            ),
        },
        "costs": {
            "base_component_fee_bps": 5.0,
            "base_component_slippage_bps": 2.0,
            "base_combined_overlay_turnover_bps": float(BASE_OVERLAY_TURNOVER_BPS),
            "stress_component_fee_bps": 10.0,
            "stress_component_slippage_bps": 5.0,
            "stress_combined_overlay_turnover_bps": float(STRESS_OVERLAY_TURNOVER_BPS),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The search scope was revised after prior 2026 diagnostics, so protocol-level "
            "selection bias remains even though the numeric ranking uses only 2021-2025.",
            "The market-state signal uses only the last complete prior UTC-day 4h snapshot.",
            "Volatility estimates use only returns closed before each exposure day; split prefixes "
            "are retained as warmup.",
            "Turnover is charged once on the combined state-times-volatility exposure.",
            "Drawdown is measured at daily closes; borrowing cost and liquidation are not modeled.",
            "This research candidate is not connected to paper or live execution.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only causal market-state exposure with a causal volatility target.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"State search: `{payload['search']['state_candidate_count']}` candidates, "
        f"shortlisted `{payload['search']['state_shortlist_count']}`; volatility grid: "
        f"`{payload['search']['volatility_candidate_count']}`.",
    ]
    if selected:
        state = selected["state"]
        config = state["config"]
        volatility = selected["volatility"]
        lines.extend(
            [
                f"Selected `{state['baseline']}` / `{state['signal']['id']}`, mode "
                f"`{config['mode']}`, threshold `{config['threshold']:.2f}`, state exposure "
                f"`{config['low_exposure']:.2f}x` / `{config['high_exposure']:.2f}x`.",
                f"Volatility target: `{volatility['lookback_days']}` days, "
                f"`{volatility['target_daily_volatility']:.2%}` daily, exposure "
                f"`{volatility['minimum_exposure']:.2f}x`–`{volatility['maximum_exposure']:.2f}x`, "
                f"`{volatility['rebalance_frequency']}`.",
                "",
                "| Split | Return | Max DD | Positive months | 15% months |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for name, result in selected["development"].items():
            lines.append(_metric_row(name, result))
    if confirmation and stress:
        lines.extend(
            [
                _metric_row("2026 reused confirmation", confirmation),
                _metric_row("2026 stress 10+5 bps", stress),
                "",
                "## 2026 Monthly Returns",
                "",
                "| Month | Base | Stress |",
                "|---|---:|---:|",
            ]
        )
        stressed = {row["label"]: row["return"] for row in stress["monthly_returns"]}
        lines.extend(
            f"| {row['label']} | {row['return']:.2%} | {stressed[row['label']]:.2%} |"
            for row in confirmation["monthly_returns"]
        )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _metric_row(label: str, result: dict[str, Any]) -> str:
    reached = sum(
        row["return"] >= float(TARGET_MONTHLY_RETURN) for row in result["monthly_returns"]
    )
    return (
        f"| {label} | {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
        f"{result['positive_month_rate']:.2%} | {reached}/{len(result['monthly_returns'])} |"
    )


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def _date_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).date().isoformat()


if __name__ == "__main__":
    main()
