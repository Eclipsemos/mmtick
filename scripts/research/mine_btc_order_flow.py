#!/usr/bin/env python3
"""Mine causal BTC order-flow factors with disjoint confirmation and cost stress."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import aggregate_bars, evaluate_targets, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.order_flow import (
    FlowCandidate,
    OrderFlowBar,
    candidate_library,
    causal_flow_features,
    flow_targets,
    load_order_flow,
)

DATABASE = Path("data/paper.db")
INTERVAL_MINUTES = 240
FEE_BPS = Decimal("5")
SLIPPAGE_BPS = Decimal("2")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/btc_order_flow/2026-08-15"),
    )
    parser.add_argument(
        "--flow-cache",
        type=Path,
        default=Path("data/order_flow_cache/btc-4h-2024-20260810-v3.json"),
    )
    args = parser.parse_args()
    periods = _periods()
    print("loading BTC 15m bars and historical funding", flush=True)
    source_bars, funding_rates = load_market(args.database, "btc_perp")
    bars = aggregate_bars(source_bars, INTERVAL_MINUTES)
    funding = funding_by_bar(bars, funding_rates)
    flow = _load_flow_cache(args.flow_cache)
    if flow is None:
        flow = load_order_flow(
            args.database,
            instrument_id="btc_perp",
            interval_minutes=INTERVAL_MINUTES,
            periods=tuple(periods.values()),
            callback=lambda stage, value: print(f"{value:.0%} {stage}", flush=True),
        )
        _write_flow_cache(args.flow_cache, flow)
    else:
        print(f"loaded {len(flow):,} cached order-flow bars", flush=True)
    research_bars = [
        bar
        for bar in bars
        if any(start_ms <= bar.start_ms <= end_ms for start_ms, end_ms in periods.values())
    ]
    coverage = (
        sum(bar.start_ms in flow for bar in research_bars) / len(research_bars)
        if research_bars
        else 0.0
    )
    features_by_window = {
        window: causal_flow_features(bars, flow, window)
        for window in {candidate.window for candidate in candidate_library()}
    }
    candidates = candidate_library()
    rows = []
    for index, candidate in enumerate(candidates, start=1):
        scores = features_by_window[candidate.window][candidate.feature]
        targets = flow_targets(scores, candidate)
        results = {
            name: evaluate_targets(
                bars,
                targets,
                start_ms=start_ms,
                end_ms=end_ms,
                funding=funding,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
            )
            for name, (start_ms, end_ms) in periods.items()
        }
        rows.append({"candidate": candidate, "results": results, "score": _score(results)})
        if index % 100 == 0:
            print(f"candidate {index}/{len(candidates)}", flush=True)

    eligible = [row for row in rows if _development_eligible(row["results"])]
    ranked = sorted(eligible or rows, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None
    top_rows = ranked[:10]
    source_selections = {
        source: _select_source(rows, source) for source in ("reported", "tick_rule")
    }
    source_comparison = {
        source: {
            "selected": _serialize_row(source_selected),
            "stress_confirmation": _stress(
                source_selected,
                bars,
                funding,
                periods["confirmation"],
                features_by_window,
            ),
        }
        for source, source_selected in source_selections.items()
    }
    stress = _stress(selected, bars, funding, periods["confirmation"], features_by_window)
    risk_ladder = _risk_ladder(selected, bars, funding, periods["confirmation"], features_by_window)
    payload = _report(
        source_bars,
        funding_rates,
        bars,
        flow,
        coverage,
        candidates,
        eligible,
        selected,
        top_rows,
        stress,
        source_comparison,
        risk_ladder,
        periods,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"btc-order-flow-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    (args.output_dir / f"{report_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / f"{report_id}.md").write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(args.output_dir / f"{report_id}.md", flush=True)


def _periods() -> dict[str, tuple[int, int]]:
    return {
        "train": (_day_start(date(2024, 1, 1)), _day_end(date(2024, 12, 31))),
        "validation": (_day_start(date(2025, 1, 1)), _day_end(date(2025, 12, 31))),
        "confirmation": (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10))),
    }


def _development_eligible(results: dict[str, Any]) -> bool:
    return all(
        result.net_return > 0 and result.max_drawdown >= -0.25 and result.completed_trades >= 6
        for result in (results["train"], results["validation"])
    )


def _score(results: dict[str, Any]) -> tuple[float, ...]:
    train = _summary(results["train"])
    validation = _summary(results["validation"])
    return (
        min(train["target_25pct_month_rate"], validation["target_25pct_month_rate"]),
        min(train["positive_month_rate"], validation["positive_month_rate"]),
        min(results["train"].net_return, results["validation"].net_return),
        results["train"].net_return + results["validation"].net_return,
        min(results["train"].max_drawdown, results["validation"].max_drawdown),
    )


def _summary(result: Any) -> dict[str, Any]:
    monthly = [{"label": label, "return": value} for label, value in result.monthly_returns]
    returns = [row["return"] for row in monthly]
    positive = sum(row["return"] > 0 for row in monthly)
    target = sum(row["return"] >= 0.25 for row in monthly)
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
        "ending_position": result.ending_position,
        "positive_month_rate": positive / len(monthly) if monthly else 0.0,
        "target_25pct_month_rate": target / len(monthly) if monthly else 0.0,
        "median_monthly_return": sorted(returns)[len(returns) // 2] if returns else 0.0,
        "worst_monthly_return": min(returns) if returns else 0.0,
        "daily_returns": [
            {"label": label, "return": value} for label, value in result.daily_returns
        ],
        "monthly_returns": monthly,
    }


def _serialize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "parameters": row["candidate"].as_dict(),
        "score": list(row["score"]),
        **{name: _summary(result) for name, result in row["results"].items()},
    }


def _select_source(rows: list[dict[str, Any]], source: str) -> dict[str, Any] | None:
    source_rows = [row for row in rows if row["candidate"].feature.startswith(f"{source}_")]
    eligible = [row for row in source_rows if _development_eligible(row["results"])]
    ranked = sorted(eligible or source_rows, key=lambda row: row["score"], reverse=True)
    return ranked[0] if ranked else None


def _stress(
    selected: dict[str, Any] | None,
    bars: list[Any],
    funding: list[list[Any]],
    period: tuple[int, int],
    features_by_window: dict[int, dict[str, tuple[Decimal | None, ...]]],
) -> dict[str, Any] | None:
    if selected is None:
        return None
    candidate: FlowCandidate = selected["candidate"]
    targets = flow_targets(features_by_window[candidate.window][candidate.feature], candidate)
    result = evaluate_targets(
        bars,
        targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=funding,
        fee_bps=STRESS_FEE_BPS,
        slippage_bps=STRESS_SLIPPAGE_BPS,
    )
    return {
        "fee_bps": float(STRESS_FEE_BPS),
        "slippage_bps": float(STRESS_SLIPPAGE_BPS),
        **_summary(result),
    }


def _risk_ladder(
    selected: dict[str, Any] | None,
    bars: list[Any],
    funding: list[list[Any]],
    period: tuple[int, int],
    features_by_window: dict[int, dict[str, tuple[Decimal | None, ...]]],
) -> list[dict[str, Any]]:
    if selected is None:
        return []
    candidate: FlowCandidate = selected["candidate"]
    targets = flow_targets(features_by_window[candidate.window][candidate.feature], candidate)
    return [
        {
            "diagnostic_only": True,
            **_summary(
                evaluate_targets(
                    bars,
                    targets,
                    start_ms=period[0],
                    end_ms=period[1],
                    funding=funding,
                    exposure=exposure,
                    fee_bps=FEE_BPS,
                    slippage_bps=SLIPPAGE_BPS,
                )
            ),
        }
        for exposure in (1.0, 1.5, 2.0, 2.5, 3.0)
    ]


def _report(
    source_bars: list[Any],
    funding_rates: list[Any],
    bars: list[Any],
    flow: dict[int, Any],
    coverage: float,
    candidates: tuple[FlowCandidate, ...],
    eligible: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    top_rows: list[dict[str, Any]],
    stress: dict[str, Any] | None,
    source_comparison: dict[str, dict[str, Any]],
    risk_ladder: list[dict[str, Any]],
    periods: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    confirmation = selected["results"]["confirmation"] if selected else None
    summary = _summary(confirmation) if confirmation else None
    approved = bool(
        summary
        and summary["net_return"] > 0
        and summary["max_drawdown"] >= -0.25
        and summary["completed_trades"] >= 6
        and summary["positive_month_rate"] >= 0.5
        and summary["target_25pct_month_rate"] >= 0.5
        and stress is not None
        and stress["net_return"] > 0
        and stress["max_drawdown"] >= -0.25
    )
    total_notional = sum((bar.total_notional for bar in flow.values()), Decimal("0"))
    reported_notional = sum((bar.reported_notional for bar in flow.values()), Decimal("0"))
    tick_rule_notional = sum((bar.tick_rule_notional for bar in flow.values()), Decimal("0"))
    unknown_notional = sum((bar.unknown_notional for bar in flow.values()), Decimal("0"))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT 4h causal order-flow factor search",
        "data": {
            "first_bar": _timestamp(source_bars[0].start_ms),
            "last_bar": _timestamp(source_bars[-1].end_ms),
            "source_bars_15m": len(source_bars),
            "bars_4h": len(bars),
            "funding_events": len(funding_rates),
            "order_flow_bars": len(flow),
            "order_flow_coverage": coverage,
            "unknown_direction_notional_rate": (
                float(unknown_notional / total_notional) if total_notional else 0.0
            ),
            "reported_direction_notional_rate": (
                float(reported_notional / total_notional) if total_notional else 0.0
            ),
            "tick_rule_direction_notional_rate": (
                float(tick_rule_notional / total_notional) if total_notional else 0.0
            ),
            "order_flow_source": "BTCUSDT archived aggregate-trade buckets",
        },
        "execution": {
            "signal_timing": "closed 4h bar",
            "fill_timing": "next 4h open",
            "fee_bps_per_fill": float(FEE_BPS),
            "slippage_bps_per_fill": float(SLIPPAGE_BPS),
            "funding": "historical BTC funding while positioned",
            "exposure": 1.0,
            "liquidation_modeled": False,
        },
        "periods": {
            name: {"start": _timestamp(start), "end": _timestamp(end)}
            for name, (start, end) in periods.items()
        },
        "selection": {
            "candidate_count": len(candidates),
            "development_eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "rule": (
                "train and validation must be positive with controlled drawdown and six trades; "
                "rank by 25% month coverage, positive month coverage, weaker return, and drawdown"
            ),
            "top_development_candidates": [_serialize_row(row) for row in top_rows],
        },
        "selected": _serialize_row(selected),
        "stress_confirmation": stress,
        "source_comparison": source_comparison,
        "risk_ladder": risk_ladder,
        "target": {
            "monthly_return": 0.25,
            "minimum_confirmation_target_month_rate": 0.5,
            "achieved": approved,
        },
        "decision": {
            "status": "research_candidate" if approved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "Order-flow candidate passed all confirmation and stress gates; it remains "
                "research-only."
                if approved
                else "No order-flow candidate passed independent confirmation, monthly target, "
                "drawdown, and cost-stress gates."
            ),
        },
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only BTC order-flow factor search.",
        "",
        f"Decision: `{payload['decision']['status']}`.",
        f"Order-flow coverage: `{payload['data']['order_flow_coverage']:.2%}`.",
        (
            "Direction notional: "
            f"reported `{payload['data']['reported_direction_notional_rate']:.2%}`; "
            f"tick-rule `{payload['data']['tick_rule_direction_notional_rate']:.2%}`."
        ),
        "",
    ]
    lines.extend(
        [
            "## Source comparison",
            "",
            "Each source is ranked independently on train and validation only.",
            "",
            "| Source | Candidate | Confirmation | Max DD | Stress | 25% months |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for source in ("reported", "tick_rule"):
        comparison = payload["source_comparison"][source]
        source_selected = comparison["selected"]
        source_stress = comparison["stress_confirmation"]
        if source_selected is None:
            lines.append(f"| {source} | none | n/a | n/a | n/a | n/a |")
            continue
        confirmation = source_selected["confirmation"]
        lines.append(
            f"| {source} | `{source_selected['parameters']['id']}` | "
            f"{confirmation['net_return']:.2%} | {confirmation['max_drawdown']:.2%} | "
            f"{source_stress['net_return']:.2%} | "
            f"{confirmation['target_25pct_month_rate']:.2%} |"
        )
    selected = payload.get("selected")
    if selected:
        parameters = selected["parameters"]
        lines.extend(
            [
                "",
                (
                    "Selected: `"
                    f"{parameters['id']}` (`{parameters['feature']}`, "
                    f"`{parameters['direction']}`, {parameters['threshold']})"
                ),
                "",
                "| Split | Return | Max DD | Trades | Positive months | 25% months |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for split in ("train", "validation", "confirmation"):
            row = selected[split]
            lines.append(
                f"| {split} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
                f"{row['completed_trades']} | {row['positive_month_rate']:.2%} | "
                f"{row['target_25pct_month_rate']:.2%} |"
            )
        lines.extend(
            ["", "### Confirmation monthly returns", "", "| Month | Return |", "|---|---:|"]
        )
        lines.extend(
            f"| {row['label']} | {row['return']:.2%} |"
            for row in selected["confirmation"]["monthly_returns"]
        )
    else:
        lines.append("No candidate was available for evaluation.")
    stress = payload.get("stress_confirmation")
    if stress:
        lines.extend(
            [
                "",
                f"Stress confirmation (10+5 bps): `{stress['net_return']:.2%}`; "
                f"max DD `{stress['max_drawdown']:.2%}`.",
            ]
        )
    if payload.get("risk_ladder"):
        lines.extend(
            [
                "",
                "### Diagnostic exposure ladder",
                "",
                "Exposure is not selected from confirmation and cannot override the 1x gate.",
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
    lines.extend(["", payload["decision"]["reason"], ""])
    return "\n".join(lines)


def _day_start(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def _load_flow_cache(path: Path) -> dict[int, OrderFlowBar] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 3:
            return None
        return {
            int(row["start_ms"]): OrderFlowBar(
                start_ms=int(row["start_ms"]),
                bucket_count=int(row["bucket_count"]),
                total_notional=Decimal(row["total_notional"]),
                buy_notional=Decimal(row["buy_notional"]),
                sell_notional=Decimal(row["sell_notional"]),
                unknown_notional=Decimal(row["unknown_notional"]),
                tick_rule_buy_notional=Decimal(row["tick_rule_buy_notional"]),
                tick_rule_sell_notional=Decimal(row["tick_rule_sell_notional"]),
            )
            for row in payload["bars"]
        }
    except (KeyError, ValueError, json.JSONDecodeError, OSError):
        return None


def _write_flow_cache(path: Path, flow: dict[int, OrderFlowBar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 3,
        "bars": [
            {
                "start_ms": row.start_ms,
                "bucket_count": row.bucket_count,
                "total_notional": str(row.total_notional),
                "buy_notional": str(row.buy_notional),
                "sell_notional": str(row.sell_notional),
                "unknown_notional": str(row.unknown_notional),
                "tick_rule_buy_notional": str(row.tick_rule_buy_notional),
                "tick_rule_sell_notional": str(row.tick_rule_sell_notional),
            }
            for row in sorted(flow.values(), key=lambda item: item.start_ms)
        ],
    }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
