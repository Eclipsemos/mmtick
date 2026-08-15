#!/usr/bin/env python3
"""Route between the frozen state strategy and BTC order flow using prior-day volatility."""

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

from mine_btc_order_flow import _load_flow_cache, _periods  # noqa: E402
from mine_defensive_factor_portfolio import (  # noqa: E402
    _development_eligible,
    _development_score,
    _period_returns,
    _strict_count,
)
from mine_fast_trend_complement import _unlocked_result  # noqa: E402
from mine_monthly_target_regime_router import (  # noqa: E402
    ASSETS,
    BASE_OVERLAY_TURNOVER_BPS,
    COMPLETE_CONFIRMATION_END,
    STRESS_OVERLAY_TURNOVER_BPS,
    TARGET_MONTHLY_RETURN,
    _period_payload,
    _state_curves,
    _timestamp,
)
from mine_order_flow_complement import (  # noqa: E402
    _candidate_replays,
    _pair_shortlist,
)

from mastermind_tick.bar_research import ResearchBar, aggregate_bars, funding_by_bar  # noqa: E402
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import (  # noqa: E402
    MonthlyRiskConfig,
    evaluate_monthly_risk_overlay,
)
from mastermind_tick.factor_portfolio import DailyReturns, PortfolioResult  # noqa: E402
from mastermind_tick.order_flow import (  # noqa: E402
    candidate_library,
    causal_flow_features,
)

VOLATILITY_LOOKBACKS = (3, 5, 10, 20)
CALIBRATION_DAYS = (60, 120, 252)
VOLATILITY_QUANTILES = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75"))
FLOW_WEIGHT_PAIRS = tuple(
    (Decimal(calm), Decimal(volatile))
    for calm, volatile in (
        ("0.25", "0"),
        ("0.5", "0"),
        ("0.75", "0"),
        ("1", "0"),
        ("0.5", "0.25"),
        ("0.75", "0.25"),
        ("1", "0.25"),
    )
)
ROUTE_SHORTLIST_SIZE = 120
LEVERAGES = tuple(Decimal(value) for value in ("1", "1.5", "2", "3", "4", "5", "6", "8"))
LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25"))
PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18"))


@dataclass(frozen=True)
class VolatilityRoute:
    flow_id: str
    volatility_lookback: int
    calibration_days: int
    quantile: Decimal
    calm_flow_weight: Decimal
    volatile_flow_weight: Decimal

    @property
    def id(self) -> str:
        quantile = str(self.quantile).replace(".", "p")
        calm = str(self.calm_flow_weight).replace(".", "p")
        volatile = str(self.volatile_flow_weight).replace(".", "p")
        return (
            f"vol-flow-{self.flow_id}-lookback{self.volatility_lookback}-"
            f"calibration{self.calibration_days}-q{quantile}-calm{calm}-volatile{volatile}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "flow_id": self.flow_id,
            "volatility_lookback_days": self.volatility_lookback,
            "calibration_days": self.calibration_days,
            "quantile": float(self.quantile),
            "calm_flow_weight": float(self.calm_flow_weight),
            "calm_state_weight": float(Decimal("1") - self.calm_flow_weight),
            "volatile_flow_weight": float(self.volatile_flow_weight),
            "volatile_state_weight": float(Decimal("1") - self.volatile_flow_weight),
        }


@dataclass(frozen=True)
class RouteRiskConfig:
    route_id: str
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"{self.route_id}-lev{self.leverage}-loss{self.loss_limit}-profit{self.profit_target}"
        )

    def risk(self, turnover_bps: Decimal) -> MonthlyRiskConfig:
        return MonthlyRiskConfig(
            leverage=self.leverage,
            loss_limit=self.loss_limit,
            profit_target=self.profit_target,
            turnover_bps=turnover_bps,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "route_id": self.route_id,
            "leverage": float(self.leverage),
            "monthly_loss_limit": float(self.loss_limit),
            "monthly_profit_target": float(self.profit_target),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--flow-cache",
        type=Path,
        default=Path("data/order_flow_cache/btc-4h-2024-20260810-v3.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/volatility_order_flow_router/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    print("loading frozen state, BTC bars, and cached order flow", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state_curves = _state_curves(loaded, args.metrics_dir)
    bars = aggregate_bars(loaded["btc_perp"][0], 240)
    funding = funding_by_bar(bars, loaded["btc_perp"][1])
    flow = _load_flow_cache(args.flow_cache)
    if flow is None:
        raise FileNotFoundError(f"missing version-3 order-flow cache: {args.flow_cache}")
    periods = _periods()

    candidates = candidate_library()
    features = {
        window: causal_flow_features(bars, flow, window)
        for window in {candidate.window for candidate in candidates}
    }
    eligible = _candidate_replays(candidates, features, bars, funding, periods)
    single_replays = {row["candidate"].id: row["returns"] for row in eligible}
    pairs = _pair_shortlist(single_replays, periods)
    flow_replays = {
        **single_replays,
        **{row["id"]: row["returns"] for row in pairs},
    }
    print(
        f"building volatility routes for {len(single_replays)} factors and {len(pairs)} pairs",
        flush=True,
    )

    daily_volatility = _daily_realized_volatility(bars)
    regimes = {
        (lookback, calibration, quantile): _prior_day_volatility_regimes(
            daily_volatility, lookback, calibration, quantile
        )
        for lookback in VOLATILITY_LOOKBACKS
        for calibration in CALIBRATION_DAYS
        for quantile in VOLATILITY_QUANTILES
    }
    route_rows = _route_shortlist(state_curves, flow_replays, regimes, periods)
    selected_routes = route_rows[:ROUTE_SHORTLIST_SIZE]
    print(f"searching monthly controls for {len(selected_routes)} development routes", flush=True)
    risk_rows = _search_risk_controls(selected_routes, periods)
    print(f"auditing {len(risk_rows):,} development-eligible controls", flush=True)
    audit = _confirmation_audit(risk_rows, periods)

    payload = _report(
        loaded,
        flow,
        candidates,
        eligible,
        pairs,
        route_rows,
        selected_routes,
        risk_rows,
        audit,
        periods,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"volatility-order-flow-router-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _daily_realized_volatility(bars: list[ResearchBar]) -> DailyReturns:
    squared: dict[str, list[Decimal]] = {}
    previous_close: Decimal | None = None
    for bar in bars:
        label = datetime.fromtimestamp(bar.start_ms / 1000, UTC).date().isoformat()
        if previous_close is not None and previous_close > 0:
            value = bar.close / previous_close - Decimal("1")
            squared.setdefault(label, []).append(value * value)
        previous_close = bar.close
    return tuple(
        (label, (sum(values, Decimal("0")) / Decimal(len(values))).sqrt())
        for label, values in sorted(squared.items())
        if values
    )


def _quantile(values: list[Decimal], quantile: Decimal) -> Decimal:
    if not values or not Decimal("0") <= quantile <= Decimal("1"):
        raise ValueError("quantile requires values and a probability in [0, 1]")
    ordered = sorted(values)
    position = Decimal(len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _prior_day_volatility_regimes(
    daily_volatility: DailyReturns,
    lookback_days: int,
    calibration_days: int,
    quantile: Decimal,
) -> dict[str, bool]:
    if lookback_days < 1 or calibration_days < 2:
        raise ValueError("volatility windows are invalid")
    rolling: list[tuple[str, Decimal]] = []
    values = [value for _label, value in daily_volatility]
    for index, (label, _value) in enumerate(daily_volatility):
        if index + 1 >= lookback_days:
            window = values[index - lookback_days + 1 : index + 1]
            rolling.append((label, sum(window, Decimal("0")) / Decimal(lookback_days)))

    regimes: dict[str, bool] = {}
    for index in range(calibration_days, len(rolling) - 1):
        _signal_label, signal = rolling[index]
        history = [value for _label, value in rolling[index - calibration_days : index]]
        target_label = rolling[index + 1][0]
        regimes[target_label] = signal <= _quantile(history, quantile)
    return regimes


def _route_returns(
    state: DailyReturns,
    flow: DailyReturns,
    regimes: dict[str, bool],
    calm_flow_weight: Decimal,
    volatile_flow_weight: Decimal,
    turnover_bps: Decimal,
) -> DailyReturns:
    if not all(
        Decimal("0") <= value <= Decimal("1") for value in (calm_flow_weight, volatile_flow_weight)
    ):
        raise ValueError("route weights must be in [0, 1]")
    state_by_label = dict(state)
    rate = turnover_bps / Decimal("10000")
    previous_weight = Decimal("0")
    result = []
    for label, flow_return in flow:
        if label not in state_by_label or label not in regimes:
            continue
        flow_weight = calm_flow_weight if regimes[label] else volatile_flow_weight
        state_weight = Decimal("1") - flow_weight
        switch_cost = abs(flow_weight - previous_weight) * rate
        routed_return = state_weight * state_by_label[label] + flow_weight * flow_return
        result.append((label, routed_return - switch_cost))
        previous_weight = flow_weight
    return tuple(result)


def _route_shortlist(
    state_curves: dict[str, DailyReturns],
    flow_replays: dict[str, dict[str, DailyReturns]],
    regimes: dict[tuple[int, int, Decimal], dict[str, bool]],
    periods: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    rows = []
    for index, (flow_id, curves) in enumerate(flow_replays.items(), start=1):
        for (lookback, calibration, quantile), regime in regimes.items():
            for calm_weight, volatile_weight in FLOW_WEIGHT_PAIRS:
                route = VolatilityRoute(
                    flow_id,
                    lookback,
                    calibration,
                    quantile,
                    calm_weight,
                    volatile_weight,
                )
                route_curves = {
                    cost: _route_returns(
                        state_curves[cost],
                        curves[cost],
                        regime,
                        calm_weight,
                        volatile_weight,
                        BASE_OVERLAY_TURNOVER_BPS
                        if cost == "base"
                        else STRESS_OVERLAY_TURNOVER_BPS,
                    )
                    for cost in ("base", "stress")
                }
                results = {
                    cost: {
                        split: _unlocked_result(_period_returns(values, periods[split]))
                        for split in ("train", "validation")
                    }
                    for cost, values in route_curves.items()
                }
                if _raw_route_eligible(results):
                    rows.append(
                        {
                            "route": route,
                            "returns": route_curves,
                            "results": results,
                            "score": _development_score(results),
                        }
                    )
        print(f"route factor {index}/{len(flow_replays)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _raw_route_eligible(results: dict[str, dict[str, PortfolioResult]]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and not result.bankrupt
        for cost_results in results.values()
        for result in cost_results.values()
    )


def _search_risk_controls(
    routes: list[dict[str, Any]], periods: dict[str, tuple[int, int]]
) -> list[dict[str, Any]]:
    rows = []
    for index, route_row in enumerate(routes, start=1):
        for leverage in LEVERAGES:
            for loss_limit in LOSS_LIMITS:
                for profit_target in PROFIT_TARGETS:
                    config = RouteRiskConfig(
                        route_row["route"].id,
                        leverage,
                        loss_limit,
                        profit_target,
                    )
                    results = {
                        cost: {
                            split: evaluate_monthly_risk_overlay(
                                _period_returns(values, periods[split]),
                                config.risk(
                                    BASE_OVERLAY_TURNOVER_BPS
                                    if cost == "base"
                                    else STRESS_OVERLAY_TURNOVER_BPS
                                ),
                            )
                            for split in ("train", "validation")
                        }
                        for cost, values in route_row["returns"].items()
                    }
                    if _development_eligible(results):
                        rows.append(
                            {
                                "route": route_row["route"],
                                "config": config,
                                "returns": route_row["returns"],
                                "results": results,
                                "score": _development_score(results),
                            }
                        )
        if index % 20 == 0:
            print(f"risk route {index}/{len(routes)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _confirmation_audit(
    rows: list[dict[str, Any]], periods: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    audited = []
    for row in rows:
        config: RouteRiskConfig = row["config"]
        results = {
            cost: evaluate_monthly_risk_overlay(
                _period_returns(values, periods["confirmation"]),
                config.risk(
                    BASE_OVERLAY_TURNOVER_BPS if cost == "base" else STRESS_OVERLAY_TURNOVER_BPS
                ),
            )
            for cost, values in row["returns"].items()
        }
        counts = {cost: _strict_count(result) for cost, result in results.items()}
        audited.append(
            {
                "row": row,
                "results": results,
                "counts": counts,
                "strict": counts["base"] == 7 and counts["stress"] == 7,
            }
        )
    ranked = sorted(
        audited,
        key=lambda item: (
            min(item["counts"].values()),
            sum(item["counts"].values()),
            item["row"]["score"],
        ),
        reverse=True,
    )
    strict = [row for row in ranked if row["strict"]]
    return {
        "configuration_count": len(audited),
        "strict_pass_count": len(strict),
        "development_selected": audited[0] if audited else None,
        "best_confirmation": ranked[0] if ranked else None,
        "strict_examples": strict[:10],
    }


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    flow: dict[int, Any],
    candidates: tuple[Any, ...],
    eligible: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    route_rows: list[dict[str, Any]],
    selected_routes: list[dict[str, Any]],
    risk_rows: list[dict[str, Any]],
    audit: dict[str, Any],
    periods: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    strict_count = audit["strict_pass_count"]
    best_count = (
        min(audit["best_confirmation"]["counts"].values()) if audit["best_confirmation"] else 0
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "prior-day BTC volatility router between frozen state and order flow",
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
            "order_flow_bars": len(flow),
            "order_flow_first": _timestamp(min(flow)),
            "order_flow_last": _timestamp(max(flow) + 4 * 60 * 60 * 1000 - 1),
        },
        "protocol": {
            "train": _period_payload(periods["train"]),
            "validation": _period_payload(periods["validation"]),
            "confirmation": _period_payload(periods["confirmation"]),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "regime_timing": "4h realized volatility through UTC day D routes day D+1",
            "selection_order": (
                "independent factor screen, raw route development rank, monthly-control "
                "development gates, reused confirmation audit"
            ),
        },
        "search": {
            "order_flow_candidate_count": len(candidates),
            "eligible_single_factor_count": len(eligible),
            "eligible_pair_count": len(pairs),
            "volatility_state_count": (
                len(VOLATILITY_LOOKBACKS) * len(CALIBRATION_DAYS) * len(VOLATILITY_QUANTILES)
            ),
            "route_grid_count": (
                (len(eligible) + len(pairs))
                * len(VOLATILITY_LOOKBACKS)
                * len(CALIBRATION_DAYS)
                * len(VOLATILITY_QUANTILES)
                * len(FLOW_WEIGHT_PAIRS)
            ),
            "development_eligible_route_count": len(route_rows),
            "route_shortlist_size": len(selected_routes),
            "risk_grid_per_route": (len(LEVERAGES) * len(LOSS_LIMITS) * len(PROFIT_TARGETS)),
            "development_risk_eligible_count": len(risk_rows),
            "top_development_routes": [_route_row_payload(row) for row in selected_routes[:20]],
            "top_development_controls": [_development_row_payload(row) for row in risk_rows[:20]],
        },
        "selection": {
            "development_selected": _audit_row_payload(audit["development_selected"]),
        },
        "confirmation_audit": {
            "configuration_count": audit["configuration_count"],
            "strict_pass_count": strict_count,
            "best_complete_month_count": best_count,
            "best_confirmation_diagnostic": _audit_row_payload(audit["best_confirmation"]),
            "strict_examples": [_audit_row_payload(row) for row in audit["strict_examples"]],
        },
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "required_complete_months": 7,
            "achieved_in_reused_confirmation": strict_count > 0,
        },
        "decision": {
            "status": (
                "reused_confirmation_candidate"
                if strict_count
                else "rejected_no_strict_monthly_solution"
            ),
            "approved_for_trading": False,
            "reason": (
                "A development-selected causal volatility route reached +15% in all seven "
                "complete reused-confirmation months under base and stress costs; fresh forward "
                "evidence remains required."
                if strict_count
                else "No development-selected causal volatility route reached +15% in all "
                "seven complete 2026 months under both base and stress costs."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "Order-flow archives provide only two complete development years.",
            "The routing hypothesis was specified after observing prior 2026 failures.",
            (
                "Allocation turnover is charged on weight changes; component trading costs "
                "remain embedded."
            ),
            "Drawdown is daily-close only; liquidation and borrowing costs are not modeled.",
        ],
    }


def _route_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "route": row["route"].as_dict(),
        "score": [float(value) for value in row["score"]],
        "development": {
            cost: {split: _result_payload(result) for split, result in values.items()}
            for cost, values in row["results"].items()
        },
    }


def _development_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "route": row["route"].as_dict(),
        "config": row["config"].as_dict(),
        "score": [float(value) for value in row["score"]],
        "development": {
            cost: {split: _result_payload(result) for split, result in values.items()}
            for cost, values in row["results"].items()
        },
    }


def _audit_row_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "route": row["row"]["route"].as_dict(),
        "config": row["row"]["config"].as_dict(),
        "counts": row["counts"],
        "development_score": [float(value) for value in row["row"]["score"]],
        "confirmation": {cost: _result_payload(result) for cost, result in row["results"].items()},
    }


def _result_payload(result: PortfolioResult) -> dict[str, Any]:
    return {
        "net_return": float(result.net_return),
        "max_drawdown": float(result.max_drawdown),
        "bankrupt": result.bankrupt,
        "monthly_returns": [
            {"label": label, "return": float(value)} for label, value in result.monthly_returns
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    lines = [
        f"# {payload['id']}",
        "",
        "Causal prior-day BTC volatility routing between the frozen state strategy and",
        "development-selected BTC order-flow sleeves.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        "## Search Result",
        "",
        f"- Raw route grid: `{payload['search']['route_grid_count']}`.",
        f"- Development-eligible raw routes: "
        f"`{payload['search']['development_eligible_route_count']}`.",
        f"- Development risk-eligible configurations: "
        f"`{payload['search']['development_risk_eligible_count']}`.",
        f"- Best reused-confirmation coverage: `{audit['best_complete_month_count']}/7`.",
        f"- Strict base-and-stress 7/7 configurations: `{audit['strict_pass_count']}`.",
        "",
        payload["decision"]["reason"],
        "Partial `2026-08` is excluded from strict counts.",
    ]
    for title, row in (
        ("Development Selection", payload["selection"]["development_selected"]),
        ("Best Confirmation Diagnostic", audit["best_confirmation_diagnostic"]),
    ):
        if row:
            lines.extend(["", f"## {title}", "", _config_line(row), ""])
            lines.extend(_monthly_table(row))
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _readme(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    return "\n".join(
        [
            "# Volatility Order-Flow Router",
            "",
            "This study uses BTC realized volatility known at the prior UTC close to route",
            "between the frozen market-state strategy and development-selected order flow.",
            "Selection uses 2024/2025 only; January-July 2026 is reused confirmation.",
            "",
            f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
            f"Best strict coverage: `{audit['best_complete_month_count']}/7`; base-and-stress "
            f"7/7 configurations: `{audit['strict_pass_count']}`.",
            "",
            "Reproduce from the repository root:",
            "",
            "```bash",
            ".venv/bin/python scripts/mine_volatility_order_flow_router.py \\",
            "  --report-id volatility-order-flow-router-20260815",
            "```",
            "",
        ]
    )


def _config_line(row: dict[str, Any]) -> str:
    route = row["route"]
    config = row["config"]
    return (
        f"`{route['flow_id']}`; volatility `{route['volatility_lookback_days']}d / "
        f"{route['calibration_days']}d / q{route['quantile']:.2f}`; calm flow/state "
        f"`{route['calm_flow_weight']:.0%}/{route['calm_state_weight']:.0%}`; volatile "
        f"flow/state `{route['volatile_flow_weight']:.0%}/{route['volatile_state_weight']:.0%}`; "
        f"leverage `{config['leverage']:.2f}x`; monthly loss/profit locks "
        f"`{config['monthly_loss_limit']:.0%}/{config['monthly_profit_target']:.0%}`."
    )


def _monthly_table(row: dict[str, Any]) -> list[str]:
    base = row["confirmation"]["base"]["monthly_returns"]
    stress = {
        item["label"]: item["return"] for item in row["confirmation"]["stress"]["monthly_returns"]
    }
    return [
        "| Month | Base | Stress |",
        "|---|---:|---:|",
        *(
            f"| {item['label']} | {item['return']:.2%} | {stress[item['label']]:.2%} |"
            for item in base
        ),
    ]


if __name__ == "__main__":
    main()
