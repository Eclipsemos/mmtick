#!/usr/bin/env python3
"""Search causal Binance market-metric exposure states for the frozen factor book."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_factor_portfolio import (
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    DISCOVERY,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    VALIDATION,
)
from train_walk_forward_factor import ANCHOR_LEVERAGE, _anchor_context, _evaluate_anchor

from mastermind_tick.bar_research import aggregate_bars, evaluate_targets, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_overlay import SignalOverlayConfig, evaluate_signal_overlay
from mastermind_tick.factor_portfolio import (
    DailyReturns,
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
)
from mastermind_tick.market_metrics import (
    METRIC_FEATURES,
    causal_metric_features,
    load_metric_archives,
    metric_targets,
    prior_utc_day_metric_signals,
)

ASSETS = {"btc_perp": "BTCUSDT", "eth_perp": "ETHUSDT"}
WINDOWS = (180, 540, 1080)
BASELINES = ("anchor", "crowding_hybrid")
TARGET_MONTHLY_RETURN = Decimal("0.15")
MIN_DEVELOPMENT_TARGET_RATE = Decimal("0.15")
MIN_CONFIRMATION_TARGET_RATE = Decimal("0.5")
DIAGNOSTIC_SIZE = 200
BASE_OVERLAY_TURNOVER_BPS = Decimal("7")
STRESS_OVERLAY_TURNOVER_BPS = Decimal("15")
DEVELOPMENT_YEARS = {
    str(year): (
        int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000),
        int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1,
    )
    for year in range(2021, 2026)
}


@dataclass(frozen=True)
class MarketStateSignal:
    asset: str
    window: int
    feature: str

    @property
    def id(self) -> str:
        return f"{self.asset}-{self.window}-{self.feature}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset": self.asset,
            "window_4h": self.window,
            "feature": self.feature,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/market_state_overlay/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading factor baselines and causal market states", flush=True)
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
            for window in WINDOWS
        }
        for asset in ASSETS
    }
    crowding_targets = metric_targets(
        features["eth_perp"][180]["top_position_crowding"],
        threshold=Decimal("2"),
        polarity="fade",
        direction="long_only",
    )
    periods = {"discovery": DISCOVERY, "validation": VALIDATION, **DEVELOPMENT_YEARS}
    baseline_results = {
        name: _baseline_results(anchor, bars, funding, crowding_targets, period, stress=False)
        for name, period in periods.items()
    }
    signals = tuple(
        MarketStateSignal(asset, window, feature)
        for asset in ASSETS
        for window in WINDOWS
        for feature in METRIC_FEATURES
    )
    daily_signals = {
        signal.id: {
            name: _daily_signals(
                bars[signal.asset],
                features[signal.asset][signal.window][signal.feature],
                baseline_results[name]["anchor"].daily_returns,
            )
            for name in periods
        }
        for signal in signals
    }

    configs = _overlay_configs()
    total = len(BASELINES) * len(signals) * len(configs)
    print(f"evaluating {total:,} market-state overlays", flush=True)
    rows = []
    for baseline in BASELINES:
        for signal in signals:
            for config in configs:
                results = {
                    name: evaluate_signal_overlay(
                        baseline_results[name][baseline].daily_returns,
                        daily_signals[signal.id][name],
                        config,
                    )
                    for name in periods
                }
                if _eligible(results):
                    rows.append(
                        {
                            "baseline": baseline,
                            "signal": signal,
                            "config": config,
                            "results": results,
                            "score": _score(results),
                        }
                    )
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
        confirmation_signals = {
            signal.id: _daily_signals(
                bars[signal.asset],
                features[signal.asset][signal.window][signal.feature],
                confirmation_baselines["anchor"].daily_returns,
            )
            for signal in signals
        }
        confirmation = _confirm(selected, confirmation_baselines, confirmation_signals)
        stress = _confirm(selected, stress_baselines, confirmation_signals, stress_overlay=True)
        for row in ranked[:DIAGNOSTIC_SIZE]:
            base_result = _confirm(row, confirmation_baselines, confirmation_signals)
            stress_result = _confirm(
                row, stress_baselines, confirmation_signals, stress_overlay=True
            )
            diagnostics.append(
                {
                    "baseline": row["baseline"],
                    "signal": row["signal"].as_dict(),
                    "config": row["config"].as_dict(),
                    "base": _public_result(base_result),
                    "stress": _public_result(stress_result),
                    "meets_confirmation_gates": _confirmation_eligible(base_result, stress_result),
                }
            )

    delayed_confirmation = None
    delayed_stress = None
    if selected:
        delayed_signals = {
            signal_id: _delay_signals(values) for signal_id, values in confirmation_signals.items()
        }
        delayed_confirmation = _confirm(selected, confirmation_baselines, delayed_signals)
        delayed_stress = _confirm(selected, stress_baselines, delayed_signals, stress_overlay=True)

    payload = _report(
        bars,
        metric_bars,
        signals,
        configs,
        ranked,
        selected,
        confirmation,
        stress,
        diagnostics,
        delayed_confirmation,
        delayed_stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"market-state-overlay-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _overlay_configs() -> tuple[SignalOverlayConfig, ...]:
    return tuple(
        SignalOverlayConfig(threshold, low, high, mode, turnover_bps=BASE_OVERLAY_TURNOVER_BPS)
        for threshold in tuple(Decimal(value) for value in ("0.5", "1", "1.5", "2"))
        for low in tuple(Decimal(value) for value in ("0.25", "0.5", "0.75", "1"))
        for high in tuple(Decimal(value) for value in ("1.25", "1.5", "2"))
        for mode in ("above", "below", "extreme", "calm")
    )


def _baseline_results(
    anchor: dict[str, Any],
    bars: dict[str, list[Any]],
    funding: dict[str, list[list[Any]]],
    crowding_targets: tuple[int | None, ...],
    period: tuple[int, int],
    *,
    stress: bool,
) -> dict[str, PortfolioResult]:
    fee = STRESS_FEE_BPS if stress else BASE_FEE_BPS
    slippage = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    anchor_result = _evaluate_anchor(anchor, period, stress=stress)
    metric = evaluate_targets(
        bars["eth_perp"],
        crowding_targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=funding["eth_perp"],
        fee_bps=fee,
        slippage_bps=slippage,
    )
    hybrid = evaluate_static_portfolio(
        {
            "anchor": anchor_result.daily_returns,
            "metric": decimal_returns(metric.daily_returns),
        },
        {"anchor": Decimal("0.4"), "metric": Decimal("0.6")},
        leverage=Decimal("1"),
    )
    return {"anchor": anchor_result, "crowding_hybrid": hybrid}


def _daily_signals(
    bars: list[Any],
    values: tuple[Decimal | None, ...],
    returns: DailyReturns,
) -> tuple[tuple[str, Decimal | None], ...]:
    return prior_utc_day_metric_signals(
        bars,
        values,
        tuple(label for label, _value in returns),
    )


def _delay_signals(
    signals: tuple[tuple[str, Decimal | None], ...],
) -> tuple[tuple[str, Decimal | None], ...]:
    return tuple(
        (label, None if index == 0 else signals[index - 1][1])
        for index, (label, _value) in enumerate(signals)
    )


def _eligible(results: dict[str, PortfolioResult]) -> bool:
    aggregates = (results["discovery"], results["validation"])
    annual = tuple(results[year] for year in DEVELOPMENT_YEARS)
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and _target_month_rate(result) >= MIN_DEVELOPMENT_TARGET_RATE
        and not result.bankrupt
        for result in (*aggregates, *annual)
    )


def _score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    discovery = results["discovery"]
    validation = results["validation"]
    annual = tuple(results[year] for year in DEVELOPMENT_YEARS)
    return (
        min(_target_month_rate(result) for result in annual),
        sum((_target_month_rate(result) for result in annual), Decimal("0")),
        min(result.positive_month_rate for result in annual),
        min(result.net_return for result in annual),
        min(_target_month_rate(discovery), _target_month_rate(validation)),
        _target_month_rate(discovery) + _target_month_rate(validation),
        min(discovery.worst_month, validation.worst_month),
        min(discovery.max_drawdown, validation.max_drawdown),
    )


def _target_month_rate(result: PortfolioResult) -> Decimal:
    if not result.monthly_returns:
        return Decimal("0")
    return Decimal(
        sum(value >= TARGET_MONTHLY_RETURN for _label, value in result.monthly_returns)
    ) / Decimal(len(result.monthly_returns))


def _public_result(result: PortfolioResult, *, include_daily: bool = False) -> dict[str, Any]:
    payload = result.as_dict(include_daily=include_daily)
    payload["target_15pct_month_rate"] = float(_target_month_rate(result))
    return payload


def _confirm(
    selected: dict[str, Any],
    baselines: dict[str, PortfolioResult],
    signals: dict[str, tuple[tuple[str, Decimal | None], ...]],
    *,
    stress_overlay: bool = False,
) -> PortfolioResult:
    config = (
        replace(selected["config"], turnover_bps=STRESS_OVERLAY_TURNOVER_BPS)
        if stress_overlay
        else selected["config"]
    )
    return evaluate_signal_overlay(
        baselines[selected["baseline"]].daily_returns,
        signals[selected["signal"].id],
        config,
    )


def _confirmation_eligible(base: PortfolioResult, stress: PortfolioResult) -> bool:
    return bool(
        _target_month_rate(base) >= MIN_CONFIRMATION_TARGET_RATE
        and _target_month_rate(stress) >= MIN_CONFIRMATION_TARGET_RATE
        and base.net_return > 0
        and stress.net_return > 0
        and base.max_drawdown >= Decimal("-0.35")
        and stress.max_drawdown >= Decimal("-0.35")
    )


def _report(
    bars: dict[str, list[Any]],
    metric_bars: dict[str, dict[int, Any]],
    signals: tuple[MarketStateSignal, ...],
    configs: tuple[SignalOverlayConfig, ...],
    ranked: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    confirmation: PortfolioResult | None,
    stress: PortfolioResult | None,
    diagnostics: list[dict[str, Any]],
    delayed_confirmation: PortfolioResult | None,
    delayed_stress: PortfolioResult | None,
) -> dict[str, Any]:
    achieved = bool(confirmation and stress and _confirmation_eligible(confirmation, stress))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "causal Binance market-state exposure overlay",
        "data": {
            asset: {
                "price_first": _timestamp(bars[asset][0].start_ms),
                "price_last": _timestamp(bars[asset][-1].end_ms),
                "metric_first": _timestamp(min(metric_bars[asset])),
                "metric_last": _timestamp(max(metric_bars[asset]) + 14_400_000 - 1),
            }
            for asset in ASSETS
        },
        "baseline": {
            "anchor_internal_leverage": float(ANCHOR_LEVERAGE),
            "crowding_hybrid_weights": {"anchor": 0.4, "metric": 0.6},
            "maximum_overlay_exposure": 2.0,
            "maximum_anchor_notional_multiple": float(ANCHOR_LEVERAGE * Decimal("2")),
        },
        "costs": {
            "base_component_fee_bps": float(BASE_FEE_BPS),
            "base_component_slippage_bps": float(BASE_SLIPPAGE_BPS),
            "base_overlay_turnover_bps": float(BASE_OVERLAY_TURNOVER_BPS),
            "stress_component_fee_bps": float(STRESS_FEE_BPS),
            "stress_component_slippage_bps": float(STRESS_SLIPPAGE_BPS),
            "stress_overlay_turnover_bps": float(STRESS_OVERLAY_TURNOVER_BPS),
        },
        "selection": {
            "candidate_count": len(BASELINES) * len(signals) * len(configs),
            "development_eligible_count": len(ranked),
            "minimum_development_target_rate": float(MIN_DEVELOPMENT_TARGET_RATE),
            "annual_consistency_required": True,
            "confirmation_used_for_selection": False,
            "selected": (
                {
                    "baseline": selected["baseline"],
                    "signal": selected["signal"].as_dict(),
                    "config": selected["config"].as_dict(),
                    "discovery": _public_result(selected["results"]["discovery"]),
                    "validation": _public_result(selected["results"]["validation"]),
                    "annual_consistency": {
                        year: _public_result(selected["results"][year])
                        for year in DEVELOPMENT_YEARS
                    },
                }
                if selected
                else None
            ),
            "top_development_configurations": [
                {
                    "baseline": row["baseline"],
                    "signal": row["signal"].as_dict(),
                    "config": row["config"].as_dict(),
                    "score": [float(value) for value in row["score"]],
                }
                for row in ranked[:20]
            ],
        },
        "confirmation": _public_result(confirmation, include_daily=True) if confirmation else None,
        "stress_confirmation": _public_result(stress) if stress else None,
        "one_day_delayed_confirmation_diagnostic": {
            "used_for_selection": False,
            "base": _public_result(delayed_confirmation) if delayed_confirmation else None,
            "stress": _public_result(delayed_stress) if delayed_stress else None,
            "meets_confirmation_gates": bool(
                delayed_confirmation
                and delayed_stress
                and _confirmation_eligible(delayed_confirmation, delayed_stress)
            ),
        },
        "confirmation_neighborhood_diagnostic": {
            "used_for_selection": False,
            "scope": f"top {DIAGNOSTIC_SIZE} development configurations",
            "configuration_count": len(diagnostics),
            "meeting_gate_count": sum(row["meets_confirmation_gates"] for row in diagnostics),
            "configurations": diagnostics,
        },
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "minimum_target_month_rate": float(MIN_CONFIRMATION_TARGET_RATE),
            "achieved": achieved,
        },
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The development-selected market-state overlay met reused base and stress "
                "confirmation gates; fresh forward evidence remains required."
                if achieved
                else "The development-selected market-state overlay failed base or stress "
                "monthly coverage, return, or drawdown gates."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The factor-book and crowding baselines were selected in prior overlapping studies.",
            "Signals use the last complete prior UTC-day 4h metric snapshot.",
            "Exposure changes cost 7 bps at base and 15 bps under stress; component costs are "
            "also included.",
            "Drawdown is measured at daily closes; borrowing cost and liquidation are not modeled.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    diagnostic = payload["confirmation_neighborhood_diagnostic"]
    delayed = payload["one_day_delayed_confirmation_diagnostic"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only causal Binance market-state exposure overlay.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Development-eligible configurations: "
        f"`{payload['selection']['development_eligible_count']}` / "
        f"`{payload['selection']['candidate_count']}`.",
        f"Non-selective confirmation diagnostic: `{diagnostic['meeting_gate_count']}` / "
        f"`{diagnostic['configuration_count']}` met gates.",
        f"One-day delayed-signal diagnostic met gates: "
        f"`{str(delayed['meets_confirmation_gates']).lower()}`.",
    ]
    if selected:
        config = selected["config"]
        lines.extend(
            [
                f"Selected baseline `{selected['baseline']}`, signal "
                f"`{selected['signal']['id']}`, mode `{config['mode']}`, threshold "
                f"`{config['threshold']:.2f}`, exposure `{config['low_exposure']:.2f}x` / "
                f"`{config['high_exposure']:.2f}x`.",
                "",
                "| Split | Return | Max DD | Positive months | 15% months |",
                "|---|---:|---:|---:|---:|",
                _metric_row("2021-2023 discovery", selected["discovery"]),
                _metric_row("2024-2025 validation", selected["validation"]),
            ]
        )
    if confirmation and stress:
        lines.extend(
            [
                _metric_row("2026 reused confirmation", confirmation),
                _metric_row("2026 stress 10+5 bps", stress),
                "",
                "## 2026 monthly returns",
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


if __name__ == "__main__":
    main()
