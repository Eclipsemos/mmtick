#!/usr/bin/env python3
"""Search a causal volatility target for the frozen ETH crowding-factor hybrid."""

from __future__ import annotations

import argparse
import json
import sys
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
from train_walk_forward_factor import _anchor_context, _evaluate_anchor

from mastermind_tick.bar_research import aggregate_bars, evaluate_targets, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_overlay import (
    VolatilityTargetConfig,
    causal_volatility_exposures,
    evaluate_volatility_target,
)
from mastermind_tick.factor_portfolio import (
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
)
from mastermind_tick.market_metrics import (
    causal_metric_features,
    load_metric_archives,
    metric_targets,
)

ASSETS = {"btc_perp": "BTCUSDT", "eth_perp": "ETHUSDT"}
TARGET_MONTHLY_RETURN = Decimal("0.15")
MIN_CONFIRMATION_TARGET_RATE = Decimal("0.5")
MIN_DEVELOPMENT_TARGET_RATE = Decimal("0.15")
BASELINE_ALLOCATIONS = {"anchor": Decimal("0.4"), "metric": Decimal("0.6")}
METRIC_WINDOW = 180
METRIC_FEATURE = "top_position_crowding"
METRIC_THRESHOLD = Decimal("2")
METRIC_POLARITY = "fade"
METRIC_DIRECTION = "long_only"
VOLATILITY_WARMUP_DAYS = 120


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/market_metric_volatility/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading frozen ETH crowding hybrid", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    bars = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
    anchor = _anchor_context(bars, loaded)
    metric_bars = load_metric_archives(args.metrics_dir, ASSETS["eth_perp"])
    metric_features = causal_metric_features(
        bars["eth_perp"], metric_bars, normalization_window=METRIC_WINDOW
    )[METRIC_FEATURE]
    targets = metric_targets(
        metric_features,
        threshold=METRIC_THRESHOLD,
        polarity=METRIC_POLARITY,
        direction=METRIC_DIRECTION,
    )
    print("evaluating volatility-target configurations", flush=True)
    periods = {"discovery": DISCOVERY, "validation": VALIDATION}
    contexts = {
        name: _period_context(anchor, bars, funding, targets, period, stress=False)
        for name, period in periods.items()
    }
    rows = []
    configs = _candidate_library()
    for config in configs:
        results = {
            name: _evaluate_period(
                contexts[name]["returns"], contexts[name]["signals"], config, period
            )
            for name, period in periods.items()
        }
        rows.append({"config": config, "results": results, "score": _score(results)})
    eligible = [row for row in rows if _eligible(row["results"])]
    ranked = sorted(eligible, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None
    confirmation = None
    stress = None
    exposures: list[dict[str, Any]] = []
    confirmation_diagnostics: list[dict[str, Any]] = []
    if selected:
        confirmation_context = _period_context(
            anchor, bars, funding, targets, CONFIRMATION, stress=False
        )
        stress_context = _period_context(anchor, bars, funding, targets, CONFIRMATION, stress=True)
        for row in ranked:
            base_result = _evaluate_period(
                confirmation_context["returns"],
                confirmation_context["signals"],
                row["config"],
                CONFIRMATION,
            )
            stress_result = _evaluate_period(
                stress_context["returns"], stress_context["signals"], row["config"], CONFIRMATION
            )
            confirmation_diagnostics.append(
                {
                    "config": row["config"].as_dict(),
                    "base": _public_result(base_result),
                    "stress": _public_result(stress_result),
                    "meets_confirmation_gates": _confirmation_eligible(base_result, stress_result),
                }
            )
        config = selected["config"]
        confirmation = _evaluate_period(
            confirmation_context["returns"],
            confirmation_context["signals"],
            config,
            CONFIRMATION,
        )
        stress = _evaluate_period(
            stress_context["returns"], stress_context["signals"], config, CONFIRMATION
        )
        exposures = _monthly_exposures(confirmation_context["signals"], config, CONFIRMATION)

    payload = _report(
        bars,
        metric_bars,
        configs,
        eligible,
        ranked,
        selected,
        confirmation,
        stress,
        exposures,
        confirmation_diagnostics,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"market-metric-volatility-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidate_library() -> tuple[VolatilityTargetConfig, ...]:
    return tuple(
        VolatilityTargetConfig(lookback, target, minimum, maximum, frequency)
        for lookback in (10, 20, 30, 60, 90)
        for target in tuple(
            Decimal(value) for value in ("0.01", "0.015", "0.02", "0.025", "0.03", "0.035", "0.04")
        )
        for minimum in tuple(Decimal(value) for value in ("0.25", "0.5", "0.75", "1"))
        for maximum in tuple(Decimal(value) for value in ("2", "2.5", "3", "3.5", "4", "5"))
        if minimum < maximum
        for frequency in ("monthly", "daily")
    )


def _baseline_returns(
    anchor: dict[str, Any],
    bars: dict[str, list[Any]],
    funding: dict[str, list[list[Any]]],
    targets: list[int],
    period: tuple[int, int],
    *,
    stress: bool,
) -> PortfolioResult:
    fee = STRESS_FEE_BPS if stress else BASE_FEE_BPS
    slippage = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    metric = evaluate_targets(
        bars["eth_perp"],
        targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=funding["eth_perp"],
        fee_bps=fee,
        slippage_bps=slippage,
    )
    anchor_result = _evaluate_anchor(anchor, period, stress=stress)
    return evaluate_static_portfolio(
        {
            "anchor": anchor_result.daily_returns,
            "metric": decimal_returns(metric.daily_returns),
        },
        BASELINE_ALLOCATIONS,
        leverage=Decimal("1"),
    )


def _period_context(
    anchor: dict[str, Any],
    bars: dict[str, list[Any]],
    funding: dict[str, list[list[Any]]],
    targets: list[int],
    period: tuple[int, int],
    *,
    stress: bool,
) -> dict[str, tuple[tuple[str, Decimal], ...]]:
    actual = _baseline_returns(anchor, bars, funding, targets, period, stress=stress)
    first_bar = max(series[0].start_ms for series in bars.values())
    warmup_period = (
        max(first_bar, period[0] - VOLATILITY_WARMUP_DAYS * 86_400_000),
        period[0] - 1,
    )
    history = (
        _baseline_returns(anchor, bars, funding, targets, warmup_period, stress=False).daily_returns
        if warmup_period[0] <= warmup_period[1]
        else ()
    )
    base_actual = (
        actual.daily_returns
        if not stress
        else _baseline_returns(anchor, bars, funding, targets, period, stress=False).daily_returns
    )
    signals = (*history, *base_actual)
    returns = (*history, *actual.daily_returns)
    if tuple(label for label, _value in signals) != tuple(label for label, _value in returns):
        raise RuntimeError("period base and stress labels are not aligned")
    return {"returns": returns, "signals": signals}


def _evaluate_period(
    returns: tuple[tuple[str, Decimal], ...],
    signal_returns: tuple[tuple[str, Decimal], ...],
    config: VolatilityTargetConfig,
    period: tuple[int, int],
) -> PortfolioResult:
    return evaluate_volatility_target(
        returns,
        config,
        signal_returns=signal_returns,
        start=_date_label(period[0]),
        end=_date_label(period[1]),
    )


def _eligible(results: dict[str, PortfolioResult]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and _target_month_rate(result) >= MIN_DEVELOPMENT_TARGET_RATE
        and not result.bankrupt
        for result in results.values()
    )


def _score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    discovery = results["discovery"]
    validation = results["validation"]
    return (
        min(_target_month_rate(discovery), _target_month_rate(validation)),
        _target_month_rate(discovery) + _target_month_rate(validation),
        min(discovery.positive_month_rate, validation.positive_month_rate),
        min(discovery.worst_month, validation.worst_month),
        min(discovery.net_return, validation.net_return),
        min(discovery.max_drawdown, validation.max_drawdown),
    )


def _target_month_rate(result: PortfolioResult) -> Decimal:
    if not result.monthly_returns:
        return Decimal("0")
    return Decimal(
        sum(value >= TARGET_MONTHLY_RETURN for _label, value in result.monthly_returns)
    ) / Decimal(len(result.monthly_returns))


def _confirmation_eligible(base: PortfolioResult, stress: PortfolioResult) -> bool:
    return bool(
        _target_month_rate(base) >= MIN_CONFIRMATION_TARGET_RATE
        and _target_month_rate(stress) >= MIN_CONFIRMATION_TARGET_RATE
        and base.net_return > 0
        and stress.net_return > 0
        and base.max_drawdown >= Decimal("-0.35")
        and stress.max_drawdown >= Decimal("-0.35")
    )


def _public_result(result: PortfolioResult, *, include_daily: bool = False) -> dict[str, Any]:
    payload = result.as_dict(include_daily=include_daily)
    payload["target_15pct_month_rate"] = float(_target_month_rate(result))
    return payload


def _monthly_exposures(
    returns: tuple[tuple[str, Decimal], ...],
    config: VolatilityTargetConfig,
    period: tuple[int, int],
) -> list[dict[str, Any]]:
    start = _date_label(period[0])
    end = _date_label(period[1])
    grouped: dict[str, list[tuple[Decimal, Decimal | None]]] = {}
    for label, exposure, volatility in causal_volatility_exposures(returns, config):
        if label < start or label > end:
            continue
        grouped.setdefault(label[:7], []).append((exposure, volatility))
    rows = []
    for month, values in grouped.items():
        exposures = tuple(exposure for exposure, _volatility in values)
        opening_volatility = values[0][1]
        rows.append(
            {
                "month": month,
                "opening_exposure": float(exposures[0]),
                "mean_exposure": float(sum(exposures, Decimal("0")) / Decimal(len(exposures))),
                "minimum_exposure": float(min(exposures)),
                "maximum_exposure": float(max(exposures)),
                "opening_trailing_daily_volatility": (
                    float(opening_volatility) if opening_volatility is not None else None
                ),
            }
        )
    return rows


def _report(
    bars: dict[str, list[Any]],
    metric_bars: dict[int, Any],
    configs: tuple[VolatilityTargetConfig, ...],
    eligible: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    confirmation: PortfolioResult | None,
    stress: PortfolioResult | None,
    exposures: list[dict[str, Any]],
    confirmation_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    achieved = bool(confirmation and stress and _confirmation_eligible(confirmation, stress))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "causal volatility target on frozen ETH crowding-factor hybrid",
        "data": {
            "price_first": _timestamp(max(series[0].start_ms for series in bars.values())),
            "price_last": _timestamp(min(series[-1].end_ms for series in bars.values())),
            "metric_first": _timestamp(min(metric_bars)),
            "metric_last": _timestamp(max(metric_bars) + 14_400_000 - 1),
            "metric_4h_bars": len(metric_bars),
        },
        "baseline": {
            "anchor_weight": float(BASELINE_ALLOCATIONS["anchor"]),
            "metric_weight": float(BASELINE_ALLOCATIONS["metric"]),
            "outer_leverage_before_overlay": 1.0,
            "metric": {
                "asset": "eth_perp",
                "normalization_window_4h": METRIC_WINDOW,
                "feature": METRIC_FEATURE,
                "threshold": float(METRIC_THRESHOLD),
                "polarity": METRIC_POLARITY,
                "direction": METRIC_DIRECTION,
            },
            "frozen_before_overlay_search": True,
        },
        "selection": {
            "candidate_count": len(configs),
            "development_eligible_count": len(eligible),
            "minimum_development_target_rate": float(MIN_DEVELOPMENT_TARGET_RATE),
            "confirmation_used_for_selection": False,
            "selected": (
                {
                    "config": selected["config"].as_dict(),
                    "discovery": _public_result(selected["results"]["discovery"]),
                    "validation": _public_result(selected["results"]["validation"]),
                }
                if selected
                else None
            ),
            "top_development_configurations": [
                {
                    "config": row["config"].as_dict(),
                    "score": [float(value) for value in row["score"]],
                    "discovery": _public_result(row["results"]["discovery"]),
                    "validation": _public_result(row["results"]["validation"]),
                }
                for row in ranked[:20]
            ],
        },
        "confirmation": _public_result(confirmation, include_daily=True) if confirmation else None,
        "stress_confirmation": _public_result(stress) if stress else None,
        "confirmation_month_opening_exposures": exposures,
        "confirmation_neighborhood_diagnostic": {
            "used_for_selection": False,
            "configuration_count": len(confirmation_diagnostics),
            "meeting_gate_count": sum(
                row["meets_confirmation_gates"] for row in confirmation_diagnostics
            ),
            "configurations": confirmation_diagnostics,
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
                "The development-selected volatility target met the reused base and stress "
                "confirmation gates; fresh forward evidence remains required."
                if achieved
                else "The development-selected volatility target failed base or stress monthly "
                "coverage, return, or drawdown gates."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The ETH crowding hybrid was selected in an earlier study using 2021-2025.",
            "Volatility uses only prior daily closes; monthly mode holds exposure through the "
            "month.",
            "Exposure changes include 7 bps turnover cost in addition to component trading costs.",
            "Drawdown is measured at daily closes; borrowing cost and liquidation are not modeled.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only causal volatility target on the frozen ETH crowding-factor hybrid.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Development-eligible configurations: "
        f"`{payload['selection']['development_eligible_count']}` / "
        f"`{payload['selection']['candidate_count']}`.",
        f"Non-selective confirmation diagnostic: "
        f"`{payload['confirmation_neighborhood_diagnostic']['meeting_gate_count']}` / "
        f"`{payload['confirmation_neighborhood_diagnostic']['configuration_count']}` met gates.",
    ]
    if selected:
        config = selected["config"]
        lines.extend(
            [
                f"Selected `{config['rebalance_frequency']}` rebalance, lookback "
                f"`{config['lookback_days']}` days, daily volatility target "
                f"`{config['target_daily_volatility']:.2%}`, exposure "
                f"`{config['minimum_exposure']:.2f}x` to `{config['maximum_exposure']:.2f}x`.",
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
                "| Month | Base | Stress | Mean exposure |",
                "|---|---:|---:|---:|",
            ]
        )
        stressed = {row["label"]: row["return"] for row in stress["monthly_returns"]}
        exposure = {
            row["month"]: row["mean_exposure"]
            for row in payload["confirmation_month_opening_exposures"]
        }
        lines.extend(
            f"| {row['label']} | {row['return']:.2%} | {stressed[row['label']]:.2%} | "
            f"{exposure[row['label']]:.2f}x |"
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


def _date_label(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).date().isoformat()


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
