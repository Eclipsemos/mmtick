#!/usr/bin/env python3
"""Mine causal BTC/ETH regime and relative-strength portfolio factors."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import aggregate_bars, funding_by_bar
from mastermind_tick.cross_asset_factor import (
    CrossAssetCandidate,
    PortfolioResult,
    candidate_library,
    causal_asset_scores,
    evaluate_portfolio_targets,
    factor_targets,
)
from mastermind_tick.factor_mining import load_market
from mastermind_tick.pair_research import PairBar, align_pair_bars


def _day_start(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


BASE_FEE_BPS = Decimal("5")
BASE_SLIPPAGE_BPS = Decimal("2")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")
DEVELOPMENT = (_day_start(date(2021, 1, 1)), _day_end(date(2025, 12, 31)))
CONFIRMATION = (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10)))
EXPOSURES = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/cross_asset_factor/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading aligned BTC/ETH bars and funding", flush=True)
    btc_source, btc_rates = load_market(args.database, "btc_perp")
    eth_source, eth_rates = load_market(args.database, "eth_perp")
    candidates = candidate_library()
    intervals = sorted({candidate.interval_minutes for candidate in candidates})
    bars_by_interval: dict[int, list[PairBar]] = {}
    funding_by_interval: dict[int, tuple[list[list[Any]], list[list[Any]]]] = {}
    for interval in intervals:
        btc = aggregate_bars(btc_source, interval)
        eth = aggregate_bars(eth_source, interval)
        bars_by_interval[interval] = align_pair_bars(btc, eth)
        funding_by_interval[interval] = (
            funding_by_bar(btc, btc_rates),
            funding_by_bar(eth, eth_rates),
        )

    feature_cache: dict[
        tuple[int, int, str],
        tuple[tuple[Decimal | None, ...], tuple[Decimal | None, ...]],
    ] = {}
    for candidate in candidates:
        key = (candidate.interval_minutes, candidate.lookback_days, candidate.feature_set)
        if key in feature_cache:
            continue
        interval = candidate.interval_minutes
        left_funding, right_funding = funding_by_interval[interval]
        feature_cache[key] = causal_asset_scores(
            bars_by_interval[interval],
            left_funding,
            right_funding,
            lookback=candidate.lookback_bars,
            normalization_window=180 * 1440 // interval,
            feature_set=candidate.feature_set,
        )
    print(f"evaluating {len(candidates):,} portfolio candidates", flush=True)
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        bars = bars_by_interval[candidate.interval_minutes]
        left_funding, right_funding = funding_by_interval[candidate.interval_minutes]
        scores = feature_cache[
            (candidate.interval_minutes, candidate.lookback_days, candidate.feature_set)
        ]
        targets = factor_targets(*scores, candidate, bars)
        result = evaluate_portfolio_targets(
            bars,
            targets,
            left_funding,
            right_funding,
            start_ms=DEVELOPMENT[0],
            end_ms=DEVELOPMENT[1],
            fee_bps=BASE_FEE_BPS,
            slippage_bps=BASE_SLIPPAGE_BPS,
        )
        rows.append(
            {
                "candidate": candidate,
                "targets": targets,
                "development": result,
                "score": _selection_score(result),
            }
        )
        if index % 250 == 0:
            print(f"candidate {index}/{len(candidates)}", flush=True)

    eligible = [row for row in rows if _development_eligible(row["development"])]
    ranked = sorted(eligible or rows, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None
    exposure_selection = _select_exposure(selected, bars_by_interval, funding_by_interval)
    confirmation = _evaluate_selected(
        selected,
        bars_by_interval,
        funding_by_interval,
        CONFIRMATION,
        exposure_selection["selected_exposure"],
    )
    stress = _evaluate_selected(
        selected,
        bars_by_interval,
        funding_by_interval,
        CONFIRMATION,
        exposure_selection["selected_exposure"],
        fee_bps=STRESS_FEE_BPS,
        slippage_bps=STRESS_SLIPPAGE_BPS,
    )
    confirmation_neighbors = [
        {
            "parameters": row["candidate"].as_dict(),
            "confirmation": _summary(
                _evaluate_row(
                    row,
                    bars_by_interval,
                    funding_by_interval,
                    CONFIRMATION,
                    exposure=1.0,
                )
            ),
        }
        for row in ranked[: min(10, len(ranked))]
    ]
    risk_ladder = (
        [
            {
                "exposure": exposure,
                **_summary(
                    _evaluate_selected(
                        selected,
                        bars_by_interval,
                        funding_by_interval,
                        CONFIRMATION,
                        exposure,
                    )
                ),
            }
            for exposure in EXPOSURES
        ]
        if selected
        else []
    )
    consensus_diagnostics = _consensus_diagnostics(
        eligible,
        bars_by_interval,
        funding_by_interval,
    )
    payload = _report(
        btc_source,
        eth_source,
        btc_rates,
        eth_rates,
        candidates,
        eligible,
        ranked[:10],
        selected,
        exposure_selection,
        confirmation,
        stress,
        confirmation_neighbors,
        risk_ladder,
        consensus_diagnostics,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"cross-asset-factor-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _development_eligible(result: PortfolioResult) -> bool:
    yearly = [value for _label, value in result.yearly_returns]
    monthly = [value for _label, value in result.monthly_returns]
    return bool(
        len(yearly) == 5
        and sum(value > 0 for value in yearly) >= 4
        and min(yearly) >= -0.10
        and result.max_drawdown >= -0.30
        and result.completed_trades >= 20
        and sum(value > 0 for value in monthly) / len(monthly) >= 0.5
        and not result.bankrupt
    )


def _selection_score(result: PortfolioResult) -> tuple[float, ...]:
    yearly = [value for _label, value in result.yearly_returns]
    monthly = [value for _label, value in result.monthly_returns]
    target_rate = sum(value >= 0.25 for value in monthly) / len(monthly) if monthly else 0.0
    positive_rate = sum(value > 0 for value in monthly) / len(monthly) if monthly else 0.0
    sorted_yearly = sorted(yearly)
    median_year = sorted_yearly[len(sorted_yearly) // 2] if sorted_yearly else -1.0
    return (
        target_rate,
        positive_rate,
        min(yearly) if yearly else -1.0,
        median_year,
        result.net_return,
        result.max_drawdown,
    )


def _select_exposure(
    selected: dict[str, Any] | None,
    bars_by_interval: dict[int, list[PairBar]],
    funding_by_interval: dict[int, tuple[list[list[Any]], list[list[Any]]]],
) -> dict[str, Any]:
    if selected is None:
        return {"selected_exposure": 1.0, "rows": []}
    rows = []
    for exposure in EXPOSURES:
        result = _evaluate_row(
            selected,
            bars_by_interval,
            funding_by_interval,
            DEVELOPMENT,
            exposure=exposure,
        )
        summary = _summary(result)
        rows.append({"exposure": exposure, **summary})
    safe = [row for row in rows if not row["bankrupt"] and row["max_drawdown"] >= -0.35]
    ranked = sorted(
        safe or rows[:1],
        key=lambda row: (
            row["target_25pct_month_rate"],
            row["positive_month_rate"],
            row["median_monthly_return"],
            row["net_return"],
        ),
        reverse=True,
    )
    return {"selected_exposure": ranked[0]["exposure"], "rows": rows}


def _evaluate_selected(
    selected: dict[str, Any] | None,
    bars_by_interval: dict[int, list[PairBar]],
    funding_by_interval: dict[int, tuple[list[list[Any]], list[list[Any]]]],
    period: tuple[int, int],
    exposure: float,
    *,
    fee_bps: Decimal = BASE_FEE_BPS,
    slippage_bps: Decimal = BASE_SLIPPAGE_BPS,
) -> PortfolioResult | None:
    if selected is None:
        return None
    return _evaluate_row(
        selected,
        bars_by_interval,
        funding_by_interval,
        period,
        exposure=exposure,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )


def _evaluate_row(
    row: dict[str, Any],
    bars_by_interval: dict[int, list[PairBar]],
    funding_by_interval: dict[int, tuple[list[list[Any]], list[list[Any]]]],
    period: tuple[int, int],
    *,
    exposure: float,
    fee_bps: Decimal = BASE_FEE_BPS,
    slippage_bps: Decimal = BASE_SLIPPAGE_BPS,
) -> PortfolioResult:
    candidate: CrossAssetCandidate = row["candidate"]
    left_funding, right_funding = funding_by_interval[candidate.interval_minutes]
    return evaluate_portfolio_targets(
        bars_by_interval[candidate.interval_minutes],
        row["targets"],
        left_funding,
        right_funding,
        start_ms=period[0],
        end_ms=period[1],
        exposure=exposure,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )


def _consensus_diagnostics(
    eligible: list[dict[str, Any]],
    bars_by_interval: dict[int, list[PairBar]],
    funding_by_interval: dict[int, tuple[list[list[Any]], list[list[Any]]]],
) -> list[dict[str, Any]]:
    groups = {
        "all_development_eligible": eligible,
        "adaptive_development_eligible": [
            row for row in eligible if row["candidate"].family == "relative_adaptive"
        ],
    }
    diagnostics = []
    for name, rows in groups.items():
        if len(rows) < 2:
            continue
        intervals = {row["candidate"].interval_minutes for row in rows}
        if len(intervals) != 1:
            continue
        interval = intervals.pop()
        bars = bars_by_interval[interval]
        targets = []
        for index in range(len(bars)):
            values = [row["targets"][index] for row in rows]
            if any(value is None for value in values):
                targets.append(None)
                continue
            targets.append(
                tuple(
                    sum((value[leg] for value in values), Decimal("0")) / Decimal(len(values))
                    for leg in (0, 1)
                )
            )
        left_funding, right_funding = funding_by_interval[interval]
        development = evaluate_portfolio_targets(
            bars,
            tuple(targets),
            left_funding,
            right_funding,
            start_ms=DEVELOPMENT[0],
            end_ms=DEVELOPMENT[1],
            fee_bps=BASE_FEE_BPS,
            slippage_bps=BASE_SLIPPAGE_BPS,
        )
        confirmation = evaluate_portfolio_targets(
            bars,
            tuple(targets),
            left_funding,
            right_funding,
            start_ms=CONFIRMATION[0],
            end_ms=CONFIRMATION[1],
            fee_bps=BASE_FEE_BPS,
            slippage_bps=BASE_SLIPPAGE_BPS,
        )
        diagnostics.append(
            {
                "name": name,
                "member_ids": [row["candidate"].id for row in rows],
                "development": _summary(development),
                "confirmation": _summary(confirmation),
            }
        )
    return diagnostics


def _summary(
    result: PortfolioResult | None, *, include_daily: bool = False
) -> dict[str, Any] | None:
    if result is None:
        return None
    monthly = [{"label": label, "return": value} for label, value in result.monthly_returns]
    returns = [row["return"] for row in monthly]
    return {
        "exposure": result.exposure,
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
        "bankrupt": result.bankrupt,
        "positive_month_rate": (
            sum(value > 0 for value in returns) / len(returns) if returns else 0.0
        ),
        "target_25pct_month_rate": (
            sum(value >= 0.25 for value in returns) / len(returns) if returns else 0.0
        ),
        "median_monthly_return": (sorted(returns)[len(returns) // 2] if returns else 0.0),
        "worst_monthly_return": min(returns) if returns else 0.0,
        "daily_returns": (
            [{"label": label, "return": value} for label, value in result.daily_returns]
            if include_daily
            else []
        ),
        "monthly_returns": monthly,
        "yearly_returns": [
            {"label": label, "return": value} for label, value in result.yearly_returns
        ],
    }


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "parameters": row["candidate"].as_dict(),
        "score": list(row["score"]),
        "development": _summary(row["development"]),
    }


def _report(
    btc_source: list[Any],
    eth_source: list[Any],
    btc_rates: list[Any],
    eth_rates: list[Any],
    candidates: tuple[CrossAssetCandidate, ...],
    eligible: list[dict[str, Any]],
    top_rows: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    exposure_selection: dict[str, Any],
    confirmation: PortfolioResult | None,
    stress: PortfolioResult | None,
    confirmation_neighbors: list[dict[str, Any]],
    risk_ladder: list[dict[str, Any]],
    consensus_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    confirmation_summary = _summary(confirmation, include_daily=True)
    stress_summary = _summary(stress)
    approved = bool(
        confirmation_summary
        and confirmation_summary["net_return"] > 0
        and confirmation_summary["max_drawdown"] >= -0.35
        and confirmation_summary["positive_month_rate"] >= 0.6
        and confirmation_summary["target_25pct_month_rate"] >= 0.5
        and not confirmation_summary["bankrupt"]
        and stress_summary
        and stress_summary["net_return"] > 0
        and stress_summary["max_drawdown"] >= -0.35
        and not stress_summary["bankrupt"]
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT/ETHUSDT causal regime and relative-strength factor portfolio",
        "data": {
            "first_bar": _timestamp(max(btc_source[0].start_ms, eth_source[0].start_ms)),
            "last_bar": _timestamp(min(btc_source[-1].end_ms, eth_source[-1].end_ms)),
            "btc_bars_15m": len(btc_source),
            "eth_bars_15m": len(eth_source),
            "btc_funding_events": len(btc_rates),
            "eth_funding_events": len(eth_rates),
        },
        "execution": {
            "signal_timing": "closed 4h or daily bar",
            "fill_timing": "next bar open",
            "fee_bps_per_fill": float(BASE_FEE_BPS),
            "slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
            "funding": "historical funding for both legs while positioned",
            "position_changes": "close all active legs then open new target; conservative costs",
            "liquidation_modeled": False,
        },
        "periods": {
            "development": {"start": _timestamp(DEVELOPMENT[0]), "end": _timestamp(DEVELOPMENT[1])},
            "confirmation": {
                "start": _timestamp(CONFIRMATION[0]),
                "end": _timestamp(CONFIRMATION[1]),
            },
        },
        "selection": {
            "candidate_count": len(candidates),
            "development_eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "development_years": [2021, 2022, 2023, 2024, 2025],
            "rule": (
                "require four of five positive development years, no year below -10%, at least "
                "half of months positive, max drawdown no worse than 30%, and 20 trades; rank by "
                "25% month coverage, positive months, worst year, median year, return, and drawdown"
            ),
            "top_development_candidates": [_serialize_row(row) for row in top_rows],
        },
        "selected": _serialize_row(selected) if selected else None,
        "development_exposure_selection": exposure_selection,
        "selected_development_at_exposure": next(
            (
                row
                for row in exposure_selection["rows"]
                if row["exposure"] == exposure_selection["selected_exposure"]
            ),
            None,
        ),
        "confirmation": confirmation_summary,
        "stress_confirmation": stress_summary,
        "confirmation_neighbors": confirmation_neighbors,
        "risk_ladder": risk_ladder,
        "consensus_diagnostics": consensus_diagnostics,
        "target": {
            "monthly_return": 0.25,
            "minimum_confirmation_target_month_rate": 0.5,
            "achieved": approved,
        },
        "decision": {
            "status": "research_candidate" if approved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The fixed portfolio and development-selected exposure passed confirmation and "
                "stress gates; it remains research-only because 2026 has been reused by prior "
                "studies."
                if approved
                else "No cross-asset factor portfolio passed the independent-style confirmation, "
                "25% monthly coverage, drawdown, and cost-stress gates."
            ),
        },
        "limitations": [
            "2026 is isolated from this search but has been inspected by earlier strategy "
            "studies, so it is not a fresh holdout.",
            "Liquidation, exchange failure, market impact, and shared margin constraints are "
            "not modeled.",
            "A passing historical result would require forward paper evidence before any "
            "trading use.",
            "Static relative reversal was added after the first confirmation inspection and is "
            "diagnostic rather than independent evidence.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only BTC/ETH causal regime and relative-strength factor portfolio.",
        "",
        f"Decision: `{payload['decision']['status']}`.",
        f"Candidates: `{payload['selection']['candidate_count']:,}`; development eligible: "
        f"`{payload['selection']['development_eligible_count']:,}`.",
        "",
    ]
    selected = payload.get("selected")
    confirmation = payload.get("confirmation")
    stress = payload.get("stress_confirmation")
    if selected and confirmation:
        development = payload["selected_development_at_exposure"]
        lines.extend(
            [
                f"Selected: `{selected['parameters']['id']}`.",
                "Development-selected exposure: "
                f"`{payload['development_exposure_selection']['selected_exposure']:.1f}x`.",
                "",
                "| Split | Return | Max DD | Trades | Positive months | 25% months |",
                "|---|---:|---:|---:|---:|---:|",
                (
                    f"| development | {development['net_return']:.2%} | "
                    f"{development['max_drawdown']:.2%} | "
                    f"{development['completed_trades']} | "
                    f"{development['positive_month_rate']:.2%} | "
                    f"{development['target_25pct_month_rate']:.2%} |"
                ),
                (
                    f"| confirmation | {confirmation['net_return']:.2%} | "
                    f"{confirmation['max_drawdown']:.2%} | {confirmation['completed_trades']} | "
                    f"{confirmation['positive_month_rate']:.2%} | "
                    f"{confirmation['target_25pct_month_rate']:.2%} |"
                ),
                "",
                f"Stress confirmation (10+5 bps): `{stress['net_return']:.2%}`; "
                f"max DD `{stress['max_drawdown']:.2%}`.",
                "",
                "## Confirmation monthly returns",
                "",
                "| Month | Return |",
                "|---|---:|",
            ]
        )
        lines.extend(
            f"| {row['label']} | {row['return']:.2%} |" for row in confirmation["monthly_returns"]
        )
        lines.extend(
            [
                "",
                "## Exposure ladder",
                "",
                "Confirmation diagnostics only; exposure was selected on development data.",
                "",
                "| Exposure | Return | Max DD | Positive months | 25% months | Bankrupt |",
                "|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in payload["risk_ladder"]:
            lines.append(
                f"| {row['exposure']:.1f}x | {row['net_return']:.2%} | "
                f"{row['max_drawdown']:.2%} | {row['positive_month_rate']:.2%} | "
                f"{row['target_25pct_month_rate']:.2%} | "
                f"{'yes' if row['bankrupt'] else 'no'} |"
            )
        if payload["consensus_diagnostics"]:
            lines.extend(
                [
                    "",
                    "## Consensus diagnostics",
                    "",
                    "Equal-weight target blends of development-eligible candidates at 1x.",
                    "",
                    "| Ensemble | Members | Development | Confirmation | Confirm DD |",
                    "|---|---:|---:|---:|---:|",
                ]
            )
            for row in payload["consensus_diagnostics"]:
                lines.append(
                    f"| {row['name']} | {len(row['member_ids'])} | "
                    f"{row['development']['net_return']:.2%} | "
                    f"{row['confirmation']['net_return']:.2%} | "
                    f"{row['confirmation']['max_drawdown']:.2%} |"
                )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
