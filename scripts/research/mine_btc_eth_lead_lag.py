#!/usr/bin/env python3
"""Mine causal 4h BTC shock factors that trade delayed ETH response."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import (
    ResearchResult,
    aggregate_bars,
    evaluate_targets,
    funding_by_bar,
)
from mastermind_tick.factor_mining import load_market
from mastermind_tick.lead_lag_factor import (
    LeadLagCandidate,
    candidate_library,
    causal_shock_scores,
    evaluate_weighted_targets,
    shock_targets,
    shock_weight_targets,
    sizing_library,
)


def _day_start(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


INTERVAL_MINUTES = 240
DEVELOPMENT = (_day_start(date(2021, 1, 1)), _day_end(date(2025, 12, 31)))
CONFIRMATION = (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10)))
BASE_FEE_BPS = Decimal("5")
BASE_SLIPPAGE_BPS = Decimal("2")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")
EXPOSURES = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/btc_eth_lead_lag/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading aligned BTC/ETH 4h bars and ETH funding", flush=True)
    btc_source, _btc_rates = load_market(args.database, "btc_perp")
    eth_source, eth_rates = load_market(args.database, "eth_perp")
    btc = aggregate_bars(btc_source, INTERVAL_MINUTES)
    eth = aggregate_bars(eth_source, INTERVAL_MINUTES)
    if len(btc) != len(eth) or any(
        left.start_ms != right.start_ms for left, right in zip(btc, eth, strict=True)
    ):
        raise ValueError("BTC and ETH 4h bars must be aligned")
    funding = funding_by_bar(eth, eth_rates)
    candidates = candidate_library()
    score_cache = {
        days: causal_shock_scores(
            btc,
            eth,
            days * 1440 // INTERVAL_MINUTES,
        )
        for days in {candidate.normalization_days for candidate in candidates}
    }

    print(f"evaluating {len(candidates):,} BTC shock candidates", flush=True)
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        targets = shock_targets(*score_cache[candidate.normalization_days], candidate)
        result = evaluate_targets(
            eth,
            targets,
            start_ms=DEVELOPMENT[0],
            end_ms=DEVELOPMENT[1],
            funding=funding,
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
        if index % 200 == 0:
            print(f"candidate {index}/{len(candidates)}", flush=True)

    eligible = [row for row in rows if _development_eligible(row["development"])]
    ranked = sorted(eligible or rows, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None
    exposure_selection = _select_exposure(selected, eth, funding)
    selected_exposure = exposure_selection["selected_exposure"]
    confirmation = _evaluate_selected(selected, eth, funding, CONFIRMATION, selected_exposure)
    stress = _evaluate_selected(
        selected,
        eth,
        funding,
        CONFIRMATION,
        selected_exposure,
        fee_bps=STRESS_FEE_BPS,
        slippage_bps=STRESS_SLIPPAGE_BPS,
    )
    confirmation_neighbors = [
        {
            "parameters": row["candidate"].as_dict(),
            "confirmation": _summary(_evaluate_row(row, eth, funding, CONFIRMATION, 1.0)),
        }
        for row in ranked[: min(10, len(ranked))]
    ]
    group_winners = _group_winners(rows, eth, funding)
    risk_ladder = (
        [
            {
                "exposure": exposure,
                **_summary(_evaluate_selected(selected, eth, funding, CONFIRMATION, exposure)),
            }
            for exposure in EXPOSURES
        ]
        if selected
        else []
    )
    dynamic_sizing = _dynamic_sizing_search(
        selected,
        score_cache,
        eth,
        funding,
    )
    payload = _report(
        btc_source,
        eth_source,
        eth_rates,
        candidates,
        eligible,
        ranked[:10],
        selected,
        exposure_selection,
        confirmation,
        stress,
        confirmation_neighbors,
        group_winners,
        risk_ladder,
        dynamic_sizing,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"btc-eth-lead-lag-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
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


def _development_eligible(result: ResearchResult) -> bool:
    yearly = [value for _label, value in _yearly_returns(result)]
    monthly = [value for _label, value in result.monthly_returns]
    return bool(
        len(yearly) == 5
        and sum(value > 0 for value in yearly) >= 4
        and min(yearly) >= -0.15
        and result.max_drawdown >= -0.35
        and result.completed_trades >= 30
        and sum(value > 0 for value in monthly) / len(monthly) >= 0.5
        and not result.bankrupt
    )


def _selection_score(result: ResearchResult) -> tuple[float, ...]:
    yearly = [value for _label, value in _yearly_returns(result)]
    monthly = [value for _label, value in result.monthly_returns]
    target_rate = sum(value >= 0.25 for value in monthly) / len(monthly) if monthly else 0.0
    positive_rate = sum(value > 0 for value in monthly) / len(monthly) if monthly else 0.0
    median_year = sorted(yearly)[len(yearly) // 2] if yearly else -1.0
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
    bars: list[Any],
    funding: list[list[Any]],
) -> dict[str, Any]:
    if selected is None:
        return {"selected_exposure": 1.0, "rows": []}
    rows = []
    for exposure in EXPOSURES:
        summary = _summary(_evaluate_row(selected, bars, funding, DEVELOPMENT, exposure))
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
    bars: list[Any],
    funding: list[list[Any]],
    period: tuple[int, int],
    exposure: float,
    *,
    fee_bps: Decimal = BASE_FEE_BPS,
    slippage_bps: Decimal = BASE_SLIPPAGE_BPS,
) -> ResearchResult | None:
    if selected is None:
        return None
    return _evaluate_row(
        selected,
        bars,
        funding,
        period,
        exposure,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )


def _evaluate_row(
    row: dict[str, Any],
    bars: list[Any],
    funding: list[list[Any]],
    period: tuple[int, int],
    exposure: float,
    *,
    fee_bps: Decimal = BASE_FEE_BPS,
    slippage_bps: Decimal = BASE_SLIPPAGE_BPS,
) -> ResearchResult:
    return evaluate_targets(
        bars,
        row["targets"],
        start_ms=period[0],
        end_ms=period[1],
        funding=funding,
        exposure=exposure,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )


def _group_winners(
    rows: list[dict[str, Any]],
    bars: list[Any],
    funding: list[list[Any]],
) -> list[dict[str, Any]]:
    result = []
    groups = sorted({(row["candidate"].direction, row["candidate"].response_gate) for row in rows})
    for direction, gate in groups:
        group = [
            row
            for row in rows
            if row["candidate"].direction == direction and row["candidate"].response_gate == gate
        ]
        winner = max(group, key=lambda row: row["score"])
        result.append(
            {
                "parameters": winner["candidate"].as_dict(),
                "development": _summary(winner["development"]),
                "confirmation": _summary(_evaluate_row(winner, bars, funding, CONFIRMATION, 1.0)),
            }
        )
    return result


def _dynamic_sizing_search(
    selected: dict[str, Any] | None,
    score_cache: dict[
        int,
        tuple[tuple[Decimal | None, ...], tuple[Decimal | None, ...]],
    ],
    bars: list[Any],
    funding: list[list[Any]],
) -> dict[str, Any] | None:
    if selected is None:
        return None
    candidate: LeadLagCandidate = selected["candidate"]
    btc_scores = score_cache[candidate.normalization_days][0]
    rows = []
    for sizing in sizing_library():
        targets = shock_weight_targets(selected["targets"], btc_scores, sizing)
        for monthly_loss_limit in (
            None,
            Decimal("0.10"),
            Decimal("0.15"),
            Decimal("0.20"),
        ):
            development = evaluate_weighted_targets(
                bars,
                targets,
                start_ms=DEVELOPMENT[0],
                end_ms=DEVELOPMENT[1],
                funding=funding,
                fee_bps=BASE_FEE_BPS,
                slippage_bps=BASE_SLIPPAGE_BPS,
                monthly_loss_limit=monthly_loss_limit,
            )
            rows.append(
                {
                    "sizing": sizing,
                    "monthly_loss_limit": monthly_loss_limit,
                    "targets": targets,
                    "development": development,
                    "score": _selection_score(development),
                }
            )
    eligible = [row for row in rows if _development_eligible(row["development"])]
    ranked = sorted(eligible or rows, key=lambda row: row["score"], reverse=True)
    chosen = ranked[0]
    confirmation = evaluate_weighted_targets(
        bars,
        chosen["targets"],
        start_ms=CONFIRMATION[0],
        end_ms=CONFIRMATION[1],
        funding=funding,
        fee_bps=BASE_FEE_BPS,
        slippage_bps=BASE_SLIPPAGE_BPS,
        monthly_loss_limit=chosen["monthly_loss_limit"],
    )
    stress = evaluate_weighted_targets(
        bars,
        chosen["targets"],
        start_ms=CONFIRMATION[0],
        end_ms=CONFIRMATION[1],
        funding=funding,
        fee_bps=STRESS_FEE_BPS,
        slippage_bps=STRESS_SLIPPAGE_BPS,
        monthly_loss_limit=chosen["monthly_loss_limit"],
    )
    return {
        "profile_count": len(rows),
        "development_eligible_count": len(eligible),
        "confirmation_used_for_selection": False,
        "selected": chosen["sizing"].as_dict(),
        "monthly_loss_limit": (
            float(chosen["monthly_loss_limit"])
            if chosen["monthly_loss_limit"] is not None
            else None
        ),
        "development": _summary(chosen["development"]),
        "confirmation": _summary(confirmation, include_daily=True),
        "stress_confirmation": _summary(stress),
        "top_development_profiles": [
            {
                "parameters": row["sizing"].as_dict(),
                "monthly_loss_limit": (
                    float(row["monthly_loss_limit"])
                    if row["monthly_loss_limit"] is not None
                    else None
                ),
                "score": list(row["score"]),
                "development": _summary(row["development"]),
            }
            for row in ranked[:10]
        ],
    }


def _summary(
    result: ResearchResult | None, *, include_daily: bool = False
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
            {"label": label, "return": value} for label, value in _yearly_returns(result)
        ],
    }


def _yearly_returns(result: ResearchResult) -> tuple[tuple[str, float], ...]:
    grouped: dict[str, float] = {}
    for label, value in result.daily_returns:
        year = label[:4]
        grouped[year] = (1 + grouped.get(year, 0.0)) * (1 + value) - 1
    return tuple(grouped.items())


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "parameters": row["candidate"].as_dict(),
        "score": list(row["score"]),
        "development": _summary(row["development"]),
    }


def _report(
    btc_source: list[Any],
    eth_source: list[Any],
    eth_rates: list[Any],
    candidates: tuple[LeadLagCandidate, ...],
    eligible: list[dict[str, Any]],
    top_rows: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    exposure_selection: dict[str, Any],
    confirmation: ResearchResult | None,
    stress: ResearchResult | None,
    confirmation_neighbors: list[dict[str, Any]],
    group_winners: list[dict[str, Any]],
    risk_ladder: list[dict[str, Any]],
    dynamic_sizing: dict[str, Any] | None,
) -> dict[str, Any]:
    confirmation_summary = _summary(confirmation, include_daily=True)
    stress_summary = _summary(stress)
    target_confirmation = (
        dynamic_sizing["confirmation"] if dynamic_sizing is not None else confirmation_summary
    )
    target_stress = (
        dynamic_sizing["stress_confirmation"] if dynamic_sizing is not None else stress_summary
    )
    approved = bool(
        target_confirmation
        and dynamic_sizing
        and dynamic_sizing["development_eligible_count"] > 0
        and dynamic_sizing["development"]["max_drawdown"] >= -0.35
        and target_confirmation["net_return"] > 0
        and target_confirmation["max_drawdown"] >= -0.35
        and target_confirmation["positive_month_rate"] >= 0.6
        and target_confirmation["target_25pct_month_rate"] >= 0.5
        and not target_confirmation["bankrupt"]
        and target_stress
        and target_stress["net_return"] > 0
        and target_stress["max_drawdown"] >= -0.35
        and not target_stress["bankrupt"]
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "4h BTC shock factor trading delayed ETH response",
        "data": {
            "first_bar": _timestamp(max(btc_source[0].start_ms, eth_source[0].start_ms)),
            "last_bar": _timestamp(min(btc_source[-1].end_ms, eth_source[-1].end_ms)),
            "btc_bars_15m": len(btc_source),
            "eth_bars_15m": len(eth_source),
            "eth_funding_events": len(eth_rates),
        },
        "screening": {
            "excluded_intervals": [15, 60],
            "reason": (
                "annual BTC lead correlations were unstable and candidate event returns did not "
                "reliably cover the 14 bps modeled round-trip cost"
            ),
            "selected_interval_minutes": INTERVAL_MINUTES,
        },
        "execution": {
            "signal_timing": "closed 4h bar",
            "fill_timing": "next 4h open",
            "traded_instrument": "ETHUSDT perpetual",
            "fee_bps_per_fill": float(BASE_FEE_BPS),
            "slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
            "funding": "historical ETH funding while positioned",
            "exit": "fixed holding period followed by one forced flat signal bar",
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
            "rule": (
                "require four of five positive development years, no year below -15%, at least "
                "half of months positive, max drawdown no worse than 35%, and 30 trades; rank by "
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
        "direction_gate_winners": group_winners,
        "risk_ladder": risk_ladder,
        "dynamic_sizing": dynamic_sizing,
        "target": {
            "monthly_return": 0.25,
            "minimum_confirmation_target_month_rate": 0.5,
            "achieved": approved,
        },
        "decision": {
            "status": "research_candidate" if approved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The fixed lead-lag factor and development-selected exposure passed confirmation "
                "and stress gates; it remains research-only because 2026 is a reused holdout."
                if approved
                else "No BTC-to-ETH lead-lag factor passed confirmation, 25% monthly coverage, "
                "drawdown, and cost-stress gates."
            ),
        },
        "limitations": [
            "2026 was isolated from this search but has been viewed in earlier project research.",
            "The study uses OHLCV bars rather than synchronized sub-second BTC and ETH trades.",
            "Liquidation, market impact, exchange failure, and shared margin are not modeled.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only 4h BTC shock factor trading delayed ETH response.",
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
                    f"{development['max_drawdown']:.2%} | {development['completed_trades']} | "
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
        dynamic = payload.get("dynamic_sizing")
        if dynamic:
            lines.extend(
                [
                    "",
                    "## Development-selected dynamic sizing",
                    "",
                    f"Profile: `{dynamic['selected']['id']}`.",
                    (
                        f"Monthly loss limit: `{dynamic['monthly_loss_limit']:.0%}`."
                        if dynamic["monthly_loss_limit"] is not None
                        else "Monthly loss limit: `disabled`."
                    ),
                    "",
                    "| Split | Return | Max DD | Positive months | 25% months |",
                    "|---|---:|---:|---:|---:|",
                    (
                        f"| development | {dynamic['development']['net_return']:.2%} | "
                        f"{dynamic['development']['max_drawdown']:.2%} | "
                        f"{dynamic['development']['positive_month_rate']:.2%} | "
                        f"{dynamic['development']['target_25pct_month_rate']:.2%} |"
                    ),
                    (
                        f"| confirmation | {dynamic['confirmation']['net_return']:.2%} | "
                        f"{dynamic['confirmation']['max_drawdown']:.2%} | "
                        f"{dynamic['confirmation']['positive_month_rate']:.2%} | "
                        f"{dynamic['confirmation']['target_25pct_month_rate']:.2%} |"
                    ),
                    "",
                    "### Dynamic sizing confirmation months",
                    "",
                    "| Month | Return |",
                    "|---|---:|",
                ]
            )
            lines.extend(
                f"| {row['label']} | {row['return']:.2%} |"
                for row in dynamic["confirmation"]["monthly_returns"]
            )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
