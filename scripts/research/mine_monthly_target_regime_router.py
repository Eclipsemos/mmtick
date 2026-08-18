#!/usr/bin/env python3
"""Audit persistent-MACD diversification against the strict monthly-return target."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_factor_portfolio import (  # noqa: E402
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    DISCOVERY,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    VALIDATION,
)
from mine_market_state_overlay import (  # noqa: E402
    ASSETS,
    BASE_OVERLAY_TURNOVER_BPS,
    STRESS_OVERLAY_TURNOVER_BPS,
    _baseline_results,
)
from mine_market_state_volatility_overlay import (  # noqa: E402
    MarketStateSignal,
    StateCandidate,
    VolatilityCandidate,
    _evaluate_combined_period,
    _evaluate_state,
)
from train_walk_forward_factor import _anchor_context  # noqa: E402

from mastermind_tick.bar_research import (  # noqa: E402
    ResearchBar,
    aggregate_bars,
    evaluate_targets,
    funding_by_bar,
    macd_targets,
)
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import SignalOverlayConfig  # noqa: E402
from mastermind_tick.factor_portfolio import (  # noqa: E402
    DailyReturns,
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
    monthly_returns,
)
from mastermind_tick.market_metrics import (  # noqa: E402
    causal_metric_features,
    load_metric_archives,
    metric_targets,
)

TARGET_MONTHLY_RETURN = Decimal("0.15")
MAX_DEVELOPMENT_DRAWDOWN = Decimal("-0.35")
COMPLETE_CONFIRMATION_END = date(2026, 7, 31)
DEVELOPMENT_PERIOD = (DISCOVERY[0], VALIDATION[1])
INTERVALS = (60, 240, 1440)
MACD_PERIODS = {
    60: ((8, 24), (12, 26), (12, 36), (16, 48), (24, 72)),
    240: ((4, 12), (6, 18), (8, 24), (12, 36), (16, 48)),
    1440: ((5, 15), (8, 24), (10, 30), (12, 36), (16, 48)),
}
SIGNAL_PERIODS = (5, 9, 14)
CONFIRMATION_BARS = (1, 2, 3)
DIRECTIONS = ("long_only", "long_short")
STATE_WEIGHTS = tuple(
    Decimal(value) for value in ("0.1", "0.25", "0.4", "0.5", "0.6", "0.75", "0.9")
)
OUTER_LEVERAGES = tuple(Decimal(value) for value in ("1", "1.5", "2", "2.5", "3", "4", "5", "6"))
ROUTE_TREND_WEIGHTS = tuple(Decimal(value) for value in ("0.1", "0.25", "0.4", "0.5", "0.75", "1"))

FROZEN_STATE = StateCandidate(
    baseline="anchor",
    signal=MarketStateSignal("eth_perp", 540, "top_retail_spread"),
    config=SignalOverlayConfig(
        threshold=Decimal("1.25"),
        low_exposure=Decimal("0.8"),
        high_exposure=Decimal("2"),
        mode="below",
        turnover_bps=BASE_OVERLAY_TURNOVER_BPS,
    ),
)
FROZEN_VOLATILITY = VolatilityCandidate(
    lookback_days=20,
    target_daily_volatility=Decimal("0.03"),
    minimum_exposure=Decimal("0.6"),
    maximum_exposure=Decimal("1.1"),
    rebalance_frequency="daily",
)


@dataclass(frozen=True)
class MacdCandidate:
    asset: str
    interval_minutes: int
    fast_period: int
    slow_period: int
    signal_period: int
    direction: str
    confirmation_bars: int
    bars: list[ResearchBar]
    funding: list[list[Any]]
    targets: tuple[int | None, ...]

    @property
    def id(self) -> str:
        return (
            f"{self.asset}-macd-{self.interval_minutes}m-{self.fast_period}-"
            f"{self.slow_period}-{self.signal_period}-{self.direction}-"
            f"confirm{self.confirmation_bars}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset": self.asset,
            "interval_minutes": self.interval_minutes,
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "signal_period": self.signal_period,
            "direction": self.direction,
            "confirmation_bars": self.confirmation_bars,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/monthly_target_regime_router/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    print("loading continuous BTC/ETH history and frozen market state", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state_curves = _state_curves(loaded, args.metrics_dir)

    print("building persistent closed-bar MACD candidates", flush=True)
    candidates = _macd_candidates(loaded)
    print(f"evaluating {len(candidates):,} candidates continuously", flush=True)
    macd_rows = [_evaluate_macd(candidate) for candidate in candidates]
    independently_profitable = [row for row in macd_rows if _independently_eligible(row)]

    print("searching development-only fixed mixtures", flush=True)
    fixed_rows = _fixed_mix_search(state_curves, independently_profitable)
    print("searching development-only causal regime routes", flush=True)
    route_rows = _route_search(state_curves, macd_rows)

    payload = _report(
        loaded,
        state_curves,
        candidates,
        macd_rows,
        independently_profitable,
        fixed_rows,
        route_rows,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"monthly-target-regime-router-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _state_curves(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]], metrics_dir: Path
) -> dict[str, DailyReturns]:
    bars = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
    anchor = _anchor_context(bars, loaded)
    metric_bars = {
        asset: load_metric_archives(metrics_dir, symbol) for asset, symbol in ASSETS.items()
    }
    eth_features = causal_metric_features(
        bars["eth_perp"], metric_bars["eth_perp"], normalization_window=540
    )
    crowding_targets = metric_targets(
        eth_features["top_position_crowding"],
        threshold=Decimal("2"),
        polarity="fade",
        direction="long_only",
    )
    features = {"eth_perp": {540: {"top_retail_spread": eth_features["top_retail_spread"]}}}
    result: dict[str, DailyReturns] = {}
    for cost_name, stress, turnover in (
        ("base", False, BASE_OVERLAY_TURNOVER_BPS),
        ("stress", True, STRESS_OVERLAY_TURNOVER_BPS),
    ):
        state = replace(
            FROZEN_STATE,
            config=replace(FROZEN_STATE.config, turnover_bps=turnover),
        )
        period_returns = []
        for period in (DEVELOPMENT_PERIOD, CONFIRMATION):
            baselines = _baseline_results(
                anchor, bars, funding, crowding_targets, period, stress=stress
            )
            raw = _evaluate_state(state, features, bars, baselines, turnover)
            combined = _evaluate_combined_period(
                state,
                features,
                bars,
                baselines,
                raw.daily_returns,
                FROZEN_VOLATILITY,
                None,
                turnover,
            )
            period_returns.extend(combined.daily_returns)
        result[cost_name] = tuple(period_returns)
    return result


def _macd_candidates(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
) -> list[MacdCandidate]:
    candidates = []
    for asset, (source_bars, funding_rates) in loaded.items():
        for interval in INTERVALS:
            bars = aggregate_bars(source_bars, interval)
            funding = funding_by_bar(bars, funding_rates)
            for fast, slow in MACD_PERIODS[interval]:
                for signal in SIGNAL_PERIODS:
                    for direction in DIRECTIONS:
                        raw = macd_targets(bars, fast, slow, signal, direction)
                        for confirmation in CONFIRMATION_BARS:
                            candidates.append(
                                MacdCandidate(
                                    asset,
                                    interval,
                                    fast,
                                    slow,
                                    signal,
                                    direction,
                                    confirmation,
                                    bars,
                                    funding,
                                    _persistent_targets(raw, confirmation),
                                )
                            )
    return candidates


def _persistent_targets(
    targets: tuple[int | None, ...], confirmation_bars: int
) -> tuple[int | None, ...]:
    """Require a direction to persist; flattening remains immediate and causal."""
    if confirmation_bars < 1:
        raise ValueError("MACD confirmation bars must be positive")
    confirmed = 0
    pending = 0
    count = 0
    result: list[int | None] = []
    for target in targets:
        if target is None:
            result.append(None)
            continue
        if target == 0:
            confirmed = pending = count = 0
        elif target == confirmed:
            pending = target
            count = confirmation_bars
        elif target == pending:
            count += 1
            if count >= confirmation_bars:
                confirmed = target
        else:
            pending = target
            count = 1
            if confirmation_bars == 1:
                confirmed = target
        result.append(confirmed if target == confirmed else 0)
    return tuple(result)


def _evaluate_macd(candidate: MacdCandidate) -> dict[str, Any]:
    results = {}
    for cost_name, fee, slippage in (
        ("base", BASE_FEE_BPS, BASE_SLIPPAGE_BPS),
        ("stress", STRESS_FEE_BPS, STRESS_SLIPPAGE_BPS),
    ):
        period_returns = []
        for period in (DEVELOPMENT_PERIOD, CONFIRMATION):
            replay = evaluate_targets(
                candidate.bars,
                candidate.targets,
                start_ms=period[0],
                end_ms=period[1],
                funding=candidate.funding,
                fee_bps=fee,
                slippage_bps=slippage,
            )
            period_returns.extend(decimal_returns(replay.daily_returns))
        results[cost_name] = tuple(period_returns)
    return {
        "candidate": candidate,
        "returns": results,
        "regimes": _prior_day_regimes(candidate),
    }


def _prior_day_regimes(candidate: MacdCandidate) -> dict[str, int]:
    """Map a date to the confirmed position state known before that UTC day starts."""
    last_by_day: dict[str, int] = {}
    for bar, target in zip(candidate.bars, candidate.targets, strict=True):
        if target is not None:
            label = datetime.fromtimestamp(bar.end_ms / 1000, UTC).date().isoformat()
            last_by_day[label] = target
    result: dict[str, int] = {}
    previous = 0
    for label in sorted(last_by_day):
        result[label] = previous
        previous = last_by_day[label]
    return result


def _independently_eligible(row: dict[str, Any]) -> bool:
    return all(
        (result := _slice_result(row["returns"]["base"], period)).net_return > 0
        and not result.bankrupt
        for period in (DISCOVERY, VALIDATION)
    )


def _fixed_mix_search(
    state_curves: dict[str, DailyReturns], macd_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for macd in macd_rows:
        for state_weight in STATE_WEIGHTS:
            for leverage in OUTER_LEVERAGES:
                development = {
                    name: _static_result(
                        state_curves["base"],
                        macd["returns"]["base"],
                        state_weight,
                        leverage,
                        period,
                    )
                    for name, period in (("discovery", DISCOVERY), ("validation", VALIDATION))
                }
                if all(_development_eligible(result) for result in development.values()):
                    rows.append(
                        {
                            "candidate": macd["candidate"],
                            "state_weight": state_weight,
                            "leverage": leverage,
                            "development": development,
                            "score": _development_score(development),
                        }
                    )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _route_search(
    state_curves: dict[str, DailyReturns], macd_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for macd in macd_rows:
        for trend_weight in ROUTE_TREND_WEIGHTS:
            for leverage in OUTER_LEVERAGES:
                development = {
                    name: _route_result(
                        state_curves["base"],
                        macd["returns"]["base"],
                        macd["regimes"],
                        trend_weight,
                        leverage,
                        period,
                        BASE_OVERLAY_TURNOVER_BPS,
                    )
                    for name, period in (
                        ("discovery", DISCOVERY),
                        ("validation", VALIDATION),
                    )
                }
                if all(_development_eligible(result) for result in development.values()):
                    rows.append(
                        {
                            "candidate": macd["candidate"],
                            "trend_weight": trend_weight,
                            "leverage": leverage,
                            "development": development,
                            "score": _development_score(development),
                        }
                    )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _static_result(
    state: DailyReturns,
    trend: DailyReturns,
    state_weight: Decimal,
    leverage: Decimal,
    period: tuple[int, int],
) -> PortfolioResult:
    start, end = _period_dates(period)
    return evaluate_static_portfolio(
        {
            "state": _slice_returns(state, start, end),
            "trend": _slice_returns(trend, start, end),
        },
        {"state": state_weight, "trend": Decimal("1") - state_weight},
        leverage=leverage,
    )


def _route_result(
    state: DailyReturns,
    trend: DailyReturns,
    regimes: dict[str, int],
    trend_weight: Decimal,
    leverage: Decimal,
    period: tuple[int, int],
    turnover_bps: Decimal,
) -> PortfolioResult:
    trend_by_date = dict(trend)
    routed = []
    previous_trend = False
    rate = turnover_bps / Decimal("10000")
    for label, state_return in state:
        use_trend = regimes.get(label, 0) != 0
        selected = (
            (Decimal("1") - trend_weight) * state_return + trend_weight * trend_by_date[label]
            if use_trend
            else state_return
        )
        switch_cost = trend_weight * rate if use_trend != previous_trend else Decimal("0")
        routed.append((label, leverage * selected - leverage * switch_cost))
        previous_trend = use_trend
    return _slice_result(tuple(routed), period)


def _slice_returns(rows: DailyReturns, start: str, end: str) -> DailyReturns:
    result = tuple((label, value) for label, value in rows if start <= label <= end)
    if not result:
        raise ValueError("return slice is empty")
    return result


def _slice_result(rows: DailyReturns, period: tuple[int, int]) -> PortfolioResult:
    start, end = _period_dates(period)
    return _result_from_returns(_slice_returns(rows, start, end))


def _result_from_returns(rows: DailyReturns) -> PortfolioResult:
    equity = Decimal("100000")
    peak = equity
    max_drawdown = Decimal("0")
    bankrupt = False
    used = []
    for label, value in rows:
        equity *= Decimal("1") + value
        used.append((label, value))
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))
        if equity <= 0:
            bankrupt = True
            break
    used_rows = tuple(used)
    return PortfolioResult(
        initial_equity=Decimal("100000"),
        final_equity=equity,
        net_return=equity / Decimal("100000") - Decimal("1"),
        max_drawdown=max_drawdown,
        bankrupt=bankrupt,
        daily_returns=used_rows,
        monthly_returns=monthly_returns(used_rows),
    )


def _development_eligible(result: PortfolioResult) -> bool:
    return bool(
        result.net_return > 0
        and result.max_drawdown >= MAX_DEVELOPMENT_DRAWDOWN
        and not result.bankrupt
    )


def _development_score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    return (
        min(_target_rate(result) for result in results.values()),
        sum((_target_rate(result) for result in results.values()), Decimal("0")),
        min(result.positive_month_rate for result in results.values()),
        min(result.worst_month for result in results.values()),
        min(result.net_return for result in results.values()),
        min(result.max_drawdown for result in results.values()),
    )


def _target_rate(result: PortfolioResult, *, complete_only: bool = False) -> Decimal:
    rows = _complete_months(result.monthly_returns) if complete_only else result.monthly_returns
    if not rows:
        return Decimal("0")
    return Decimal(sum(value >= TARGET_MONTHLY_RETURN for _label, value in rows)) / Decimal(
        len(rows)
    )


def _complete_months(rows: DailyReturns) -> DailyReturns:
    return tuple(
        (label, value)
        for label, value in rows
        if date.fromisoformat(f"{label}-01") <= COMPLETE_CONFIRMATION_END.replace(day=1)
    )


def _period_dates(period: tuple[int, int]) -> tuple[str, str]:
    return (
        datetime.fromtimestamp(period[0] / 1000, UTC).date().isoformat(),
        datetime.fromtimestamp(period[1] / 1000, UTC).date().isoformat(),
    )


def _confirmation_result(
    row: dict[str, Any], state_curves: dict[str, DailyReturns], cost_name: str, kind: str
) -> PortfolioResult:
    candidate = row["candidate"]
    macd = row["macd_lookup"] if "macd_lookup" in row else None
    if macd is None:
        raise ValueError(f"missing MACD replay for {candidate.id}")
    if kind == "fixed":
        return _static_result(
            state_curves[cost_name],
            macd["returns"][cost_name],
            row["state_weight"],
            row["leverage"],
            CONFIRMATION,
        )
    turnover = BASE_OVERLAY_TURNOVER_BPS if cost_name == "base" else STRESS_OVERLAY_TURNOVER_BPS
    return _route_result(
        state_curves[cost_name],
        macd["returns"][cost_name],
        macd["regimes"],
        row["trend_weight"],
        row["leverage"],
        CONFIRMATION,
        turnover,
    )


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    state_curves: dict[str, DailyReturns],
    candidates: list[MacdCandidate],
    macd_rows: list[dict[str, Any]],
    independently_profitable: list[dict[str, Any]],
    fixed_rows: list[dict[str, Any]],
    route_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    lookup = {row["candidate"].id: row for row in independently_profitable}
    all_lookup = {row["candidate"].id: row for row in macd_rows}
    # Search rows intentionally carry only compact references; attach replay data temporarily.
    for row in (*fixed_rows, *route_rows):
        row["macd_lookup"] = lookup.get(row["candidate"].id) or all_lookup.get(row["candidate"].id)

    fixed_audit = _audit_family(fixed_rows, state_curves, "fixed")
    route_audit = _audit_family(route_rows, state_curves, "route")
    for row in (*fixed_rows, *route_rows):
        row.pop("macd_lookup", None)

    state_results = {
        name: _public_result(_slice_result(state_curves["base"], period))
        for name, period in (
            ("discovery", DISCOVERY),
            ("validation", VALIDATION),
            ("confirmation", CONFIRMATION),
        )
    }
    achieved = bool(fixed_audit["strict_pass_count"] or route_audit["strict_pass_count"])
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "frozen market-state baseline diversified or routed by persistent MACD",
        "data": {
            asset: {
                "first_bar": _timestamp(values[0][0].start_ms),
                "last_bar": _timestamp(values[0][-1].end_ms),
            }
            for asset, values in loaded.items()
        },
        "protocol": {
            "development": {
                "discovery": _period_payload(DISCOVERY),
                "validation": _period_payload(VALIDATION),
            },
            "confirmation": _period_payload(CONFIRMATION),
            "strict_confirmation_months": "2026-01 through 2026-07",
            "partial_august_excluded": True,
            "continuous_warmup": (
                "features use full pre-period bar history; development account state and "
                "volatility estimates run continuously across 2021-2025 before split slicing; "
                "the reused confirmation account resets at 2026-01-01"
            ),
            "confirmation_used_for_selection": False,
        },
        "frozen_state": {
            "state": FROZEN_STATE.as_dict(),
            "volatility": FROZEN_VOLATILITY.as_dict(),
            "results": state_results,
        },
        "search": {
            "macd_candidate_count": len(candidates),
            "independently_development_profitable_count": len(independently_profitable),
            "fixed_mix_development_eligible_count": len(fixed_rows),
            "route_development_eligible_count": len(route_rows),
            "fixed_state_weights": [float(value) for value in STATE_WEIGHTS],
            "route_trend_weights": [float(value) for value in ROUTE_TREND_WEIGHTS],
            "outer_leverages": [float(value) for value in OUTER_LEVERAGES],
        },
        "fixed_mix": fixed_audit,
        "regime_route": route_audit,
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "required_complete_months": 7,
            "achieved": achieved,
        },
        "decision": {
            "status": "rejected_no_strict_monthly_solution" if not achieved else "diagnostic_only",
            "approved_for_trading": False,
            "reason": (
                "No development-eligible fixed mixture or causal regime route reached +15% in "
                "all seven complete 2026 months under both base and stress costs."
                if not achieved
                else "A 7/7 result was observed only in reused confirmation and cannot be "
                "treated as a fresh or trading-approved strategy."
            ),
        },
        "costs": {
            "base_component_fee_bps": float(BASE_FEE_BPS),
            "base_component_slippage_bps": float(BASE_SLIPPAGE_BPS),
            "base_route_switch_bps": float(BASE_OVERLAY_TURNOVER_BPS),
            "stress_component_fee_bps": float(STRESS_FEE_BPS),
            "stress_component_slippage_bps": float(STRESS_SLIPPAGE_BPS),
            "stress_route_switch_bps": float(STRESS_OVERLAY_TURNOVER_BPS),
        },
        "limitations": [
            "2026 has been repeatedly inspected and is reused confirmation evidence, not a "
            "fresh holdout.",
            "The frozen market-state baseline came from prior research using overlapping data.",
            "Routing is selected from the persistent MACD state known by the prior UTC day close.",
            "Daily sleeve returns approximate strategy switching; route transitions incur "
            "explicit cost.",
            "Drawdown is measured at daily closes; liquidation and borrowing costs are not "
            "modeled.",
        ],
    }


def _audit_family(
    rows: list[dict[str, Any]], state_curves: dict[str, DailyReturns], kind: str
) -> dict[str, Any]:
    audited = []
    for row in rows:
        base = _confirmation_result(row, state_curves, "base", kind)
        stress = _confirmation_result(row, state_curves, "stress", kind)
        base_count = _target_count(base)
        stress_count = _target_count(stress)
        audited.append((min(base_count, stress_count), base_count, stress_count, row, base, stress))
    audited.sort(key=lambda item: (item[0], item[1], item[2], item[3]["score"]), reverse=True)
    best = audited[0] if audited else None
    strict = [item for item in audited if item[1] == 7 and item[2] == 7]
    return {
        "audited_configuration_count": len(audited),
        "strict_pass_count": len(strict),
        "best_complete_month_count": best[0] if best else 0,
        "development_selected": _audit_payload(audited, rows[0] if rows else None),
        "best_confirmation_diagnostic": _audit_payload(
            [best] if best else [], best[3] if best else None
        ),
    }


def _audit_payload(audited: list[Any], row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = next((candidate for candidate in audited if candidate[3] is row), None)
    if item is None:
        return None
    _minimum, base_count, stress_count, _row, base, stress = item
    payload = {
        "candidate": row["candidate"].as_dict(),
        "outer_leverage": float(row["leverage"]),
        "development": {
            name: _public_result(result) for name, result in row["development"].items()
        },
        "confirmation_base": _public_result(base, complete_only=True),
        "confirmation_stress": _public_result(stress, complete_only=True),
        "base_target_months": base_count,
        "stress_target_months": stress_count,
    }
    if "state_weight" in row:
        payload["state_weight"] = float(row["state_weight"])
    if "trend_weight" in row:
        payload["active_trend_weight"] = float(row["trend_weight"])
    return payload


def _target_count(result: PortfolioResult) -> int:
    return sum(
        value >= TARGET_MONTHLY_RETURN for _label, value in _complete_months(result.monthly_returns)
    )


def _public_result(result: PortfolioResult, *, complete_only: bool = False) -> dict[str, Any]:
    payload = result.as_dict()
    rows = _complete_months(result.monthly_returns) if complete_only else result.monthly_returns
    payload["monthly_returns"] = [{"label": label, "return": float(value)} for label, value in rows]
    payload["target_15pct_month_rate"] = float(
        Decimal(sum(value >= TARGET_MONTHLY_RETURN for _label, value in rows)) / Decimal(len(rows))
        if rows
        else Decimal("0")
    )
    return payload


def _period_payload(period: tuple[int, int]) -> dict[str, str]:
    start, end = _period_dates(period)
    return {"start": start, "end": end}


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['id']}",
        "",
        "Persistent-MACD diversification audit for the frozen causal market-state strategy.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        "## Strict Result",
        "",
        "| Family | Development eligible | Best base/stress complete months | Strict 7/7 |",
        "|---|---:|---:|---:|",
        _family_row("Fixed mixture", payload["fixed_mix"]),
        _family_row("Causal regime route", payload["regime_route"]),
        "",
        payload["decision"]["reason"],
        "Partial `2026-08` is excluded from every strict count.",
        "",
        "## Frozen Baseline",
        "",
        "| Split | Return | Max DD | 15% months |",
        "|---|---:|---:|---:|",
    ]
    for label, result in payload["frozen_state"]["results"].items():
        reached = sum(row["return"] >= 0.15 for row in result["monthly_returns"])
        lines.append(
            f"| {label} | {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
            f"{reached}/{len(result['monthly_returns'])} |"
        )
    for title, key in (("Fixed Mixture", "fixed_mix"), ("Causal Regime Route", "regime_route")):
        lines.extend(["", f"## {title}", ""])
        for label, section in (
            ("Development-selected", payload[key]["development_selected"]),
            ("Best reused-confirmation diagnostic", payload[key]["best_confirmation_diagnostic"]),
        ):
            if section:
                lines.extend(_configuration_lines(label, section))
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _readme(payload: dict[str, Any]) -> str:
    fixed = payload["fixed_mix"]
    route = payload["regime_route"]
    return "\n".join(
        [
            "# Monthly Target Regime Router Audit",
            "",
            "This directory records the reproducible test of persistent MACD as either a fixed",
            "sleeve or a causal router around the frozen market-state strategy.",
            "",
            f"Decision: `{payload['decision']['status']}`. No strategy is approved for trading.",
            "",
            "| Family | Eligible configs | Best strict months | 7/7 configs |",
            "|---|---:|---:|---:|",
            _readme_family_row("Fixed mixture", fixed),
            _readme_family_row("Causal regime route", route),
            "",
            "Features use full bar-history warmup and development runs continuously before split",
            "slicing. Selection uses 2021-2025 only; the confirmation account resets at",
            "2026-01-01.",
            "January-July 2026 is reused confirmation, and partial August is excluded. The result",
            "rejects this direction as a solution to the strict every-month +15% requirement.",
            "",
            "Reproduce from the repository root:",
            "",
            "```bash",
            ".venv/bin/python scripts/research/mine_monthly_target_regime_router.py \\",
            "  --report-id monthly-target-regime-router-20260815",
            "```",
            "",
        ]
    )


def _readme_family_row(label: str, section: dict[str, Any]) -> str:
    return (
        f"| {label} | {section['audited_configuration_count']} | "
        f"{section['best_complete_month_count']}/7 | {section['strict_pass_count']} |"
    )


def _family_row(label: str, section: dict[str, Any]) -> str:
    return (
        f"| {label} | {section['audited_configuration_count']} | "
        f"{section['best_complete_month_count']}/7 | {section['strict_pass_count']} |"
    )


def _configuration_lines(label: str, section: dict[str, Any]) -> list[str]:
    candidate = section["candidate"]
    lines = [
        f"**{label}:** `{candidate['id']}`, outer leverage `{section['outer_leverage']:.2f}x`"
        + _configuration_suffix(section),
        "",
        "| Month | Base | Stress |",
        "|---|---:|---:|",
    ]
    stressed = {
        row["label"]: row["return"] for row in section["confirmation_stress"]["monthly_returns"]
    }
    lines.extend(
        f"| {row['label']} | {row['return']:.2%} | {stressed[row['label']]:.2%} |"
        for row in section["confirmation_base"]["monthly_returns"]
    )
    return lines


def _configuration_suffix(section: dict[str, Any]) -> str:
    if "state_weight" in section:
        return f", state weight `{section['state_weight']:.2%}`."
    if "active_trend_weight" in section:
        return f", active trend weight `{section['active_trend_weight']:.2%}`."
    return "."


if __name__ == "__main__":
    main()
