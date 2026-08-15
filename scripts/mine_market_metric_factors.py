#!/usr/bin/env python3
"""Mine Binance futures market metrics as a marginal BTC/ETH factor sleeve."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

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
from mastermind_tick.factor_portfolio import (
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
)
from mastermind_tick.market_metrics import (
    METRIC_FEATURES,
    causal_metric_features,
    load_metric_archives,
    metric_targets,
)

ASSETS = {"btc_perp": "BTCUSDT", "eth_perp": "ETHUSDT"}
WINDOWS = (180, 540, 1080)
THRESHOLDS = tuple(Decimal(value) for value in ("0.5", "1", "1.5", "2", "2.5"))
POLARITIES = ("follow", "fade")
DIRECTIONS = ("long_only", "long_short")
ANCHOR_WEIGHTS = tuple(Decimal(value) for value in ("0.25", "0.4", "0.5", "0.6", "0.75", "0.9"))
HYBRID_LEVERAGES = tuple(
    Decimal(value) for value in ("0.5", "0.75", "1", "1.25", "1.5", "1.75", "2", "2.25", "2.5")
)
SHORTLIST_SIZE = 100
TARGET_MONTHLY_RETURN = Decimal("0.15")
MIN_TARGET_MONTH_RATE = Decimal("0.5")
MIN_DEVELOPMENT_TARGET_RATE = Decimal("0.15")


@dataclass(frozen=True)
class MetricCandidate:
    asset: str
    window: int
    feature: str
    threshold: Decimal
    polarity: str
    direction: str

    @property
    def id(self) -> str:
        threshold = f"{self.threshold:g}".replace(".", "p")
        return (
            f"{self.asset}-{self.window}-{self.feature}-{threshold}-"
            f"{self.polarity}-{self.direction}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset": self.asset,
            "normalization_window_4h": self.window,
            "feature": self.feature,
            "threshold": float(self.threshold),
            "polarity": self.polarity,
            "direction": self.direction,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/market_metric_factor/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading aligned BTC/ETH bars and frozen anchor", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    bars = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
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
    anchor = _anchor_context(bars, loaded)
    periods = {"discovery": DISCOVERY, "validation": VALIDATION}
    anchor_results = {
        split: _evaluate_anchor(anchor, period, stress=False) for split, period in periods.items()
    }

    rows = []
    candidates = tuple(_candidate_library())
    print(f"evaluating {len(candidates):,} metric candidates", flush=True)
    for index, candidate in enumerate(candidates, start=1):
        targets = metric_targets(
            features[candidate.asset][candidate.window][candidate.feature],
            threshold=candidate.threshold,
            polarity=candidate.polarity,
            direction=candidate.direction,
        )
        results = {
            split: evaluate_targets(
                bars[candidate.asset],
                targets,
                start_ms=period[0],
                end_ms=period[1],
                funding=funding[candidate.asset],
                fee_bps=BASE_FEE_BPS,
                slippage_bps=BASE_SLIPPAGE_BPS,
            )
            for split, period in periods.items()
        }
        rows.append(
            {
                "candidate": candidate,
                "targets": targets,
                "results": results,
                "score": _metric_score(results),
            }
        )
        if index % 200 == 0:
            print(f"candidate {index}/{len(candidates)}", flush=True)
    eligible = [row for row in rows if _metric_eligible(row["results"])]
    shortlist = sorted(eligible, key=lambda row: row["score"], reverse=True)[:SHORTLIST_SIZE]
    hybrid_rows = _hybrid_search(shortlist, anchor_results)
    selected = hybrid_rows[0] if hybrid_rows else None
    confirmation = _confirm(selected, anchor, bars, funding, stress=False)
    stress = _confirm(selected, anchor, bars, funding, stress=True)
    payload = _report(
        bars,
        metric_bars,
        candidates,
        eligible,
        shortlist,
        hybrid_rows,
        selected,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"market-metric-factor-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidate_library() -> list[MetricCandidate]:
    return [
        MetricCandidate(asset, window, feature, threshold, polarity, direction)
        for asset in ASSETS
        for window in WINDOWS
        for feature in METRIC_FEATURES
        for threshold in THRESHOLDS
        for polarity in POLARITIES
        for direction in DIRECTIONS
    ]


def _metric_eligible(results: dict[str, Any]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= -0.50
        and result.completed_trades >= 12
        and not result.bankrupt
        for result in results.values()
    )


def _metric_score(results: dict[str, Any]) -> tuple[Decimal, ...]:
    summaries = {name: _research_summary(result) for name, result in results.items()}
    discovery = summaries["discovery"]
    validation = summaries["validation"]
    return (
        min(discovery["target_month_rate"], validation["target_month_rate"]),
        min(discovery["positive_month_rate"], validation["positive_month_rate"]),
        min(
            Decimal(str(results["discovery"].net_return)),
            Decimal(str(results["validation"].net_return)),
        ),
        min(
            Decimal(str(results["discovery"].max_drawdown)),
            Decimal(str(results["validation"].max_drawdown)),
        ),
    )


def _hybrid_search(
    shortlist: list[dict[str, Any]], anchor_results: dict[str, PortfolioResult]
) -> list[dict[str, Any]]:
    rows = []
    for index, metric in enumerate(shortlist, start=1):
        for anchor_weight in ANCHOR_WEIGHTS:
            allocations = {"anchor": anchor_weight, "metric": Decimal("1") - anchor_weight}
            for leverage in HYBRID_LEVERAGES:
                results = {
                    split: evaluate_static_portfolio(
                        {
                            "anchor": anchor_results[split].daily_returns,
                            "metric": decimal_returns(metric["results"][split].daily_returns),
                        },
                        allocations,
                        leverage=leverage,
                    )
                    for split in ("discovery", "validation")
                }
                if _hybrid_eligible(results):
                    rows.append(
                        {
                            "metric": metric,
                            "anchor_weight": anchor_weight,
                            "leverage": leverage,
                            "results": results,
                            "score": _portfolio_score(results),
                        }
                    )
        if index % 20 == 0:
            print(f"hybrid shortlist {index}/{len(shortlist)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _hybrid_eligible(results: dict[str, PortfolioResult]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and _target_month_rate(result) >= MIN_DEVELOPMENT_TARGET_RATE
        and not result.bankrupt
        for result in results.values()
    )


def _portfolio_score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
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


def _confirm(
    selected: dict[str, Any] | None,
    anchor: dict[str, Any],
    bars: dict[str, list[Any]],
    funding: dict[str, list[list[Any]]],
    *,
    stress: bool,
) -> PortfolioResult | None:
    if selected is None:
        return None
    metric = selected["metric"]
    candidate: MetricCandidate = metric["candidate"]
    fee = STRESS_FEE_BPS if stress else BASE_FEE_BPS
    slippage = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    result = evaluate_targets(
        bars[candidate.asset],
        metric["targets"],
        start_ms=CONFIRMATION[0],
        end_ms=CONFIRMATION[1],
        funding=funding[candidate.asset],
        fee_bps=fee,
        slippage_bps=slippage,
    )
    anchor_result = _evaluate_anchor(anchor, CONFIRMATION, stress=stress)
    return evaluate_static_portfolio(
        {
            "anchor": anchor_result.daily_returns,
            "metric": decimal_returns(result.daily_returns),
        },
        {
            "anchor": selected["anchor_weight"],
            "metric": Decimal("1") - selected["anchor_weight"],
        },
        leverage=selected["leverage"],
    )


def _research_summary(result: Any) -> dict[str, Decimal]:
    monthly = tuple(Decimal(str(value)) for _label, value in result.monthly_returns)
    return {
        "positive_month_rate": Decimal(sum(value > 0 for value in monthly)) / Decimal(len(monthly)),
        "target_month_rate": Decimal(sum(value >= TARGET_MONTHLY_RETURN for value in monthly))
        / Decimal(len(monthly)),
    }


def _target_month_rate(result: PortfolioResult) -> Decimal:
    if not result.monthly_returns:
        return Decimal("0")
    return Decimal(
        sum(value >= TARGET_MONTHLY_RETURN for _label, value in result.monthly_returns)
    ) / Decimal(len(result.monthly_returns))


def _report(
    bars: dict[str, list[Any]],
    metric_bars: dict[str, dict[int, Any]],
    candidates: tuple[MetricCandidate, ...],
    eligible: list[dict[str, Any]],
    shortlist: list[dict[str, Any]],
    hybrid_rows: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    confirmation: PortfolioResult | None,
    stress: PortfolioResult | None,
) -> dict[str, Any]:
    achieved = bool(
        confirmation
        and stress
        and _target_month_rate(confirmation) >= MIN_TARGET_MONTH_RATE
        and _target_month_rate(stress) >= MIN_TARGET_MONTH_RATE
        and confirmation.max_drawdown >= Decimal("-0.35")
        and stress.net_return > 0
        and stress.max_drawdown >= Decimal("-0.35")
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "Binance market-metric sleeve plus frozen four-factor anchor",
        "data": {
            asset: {
                "price_first": _timestamp(series[0].start_ms),
                "price_last": _timestamp(series[-1].end_ms),
                "metric_first": _timestamp(min(metric_bars[asset])),
                "metric_last": _timestamp(max(metric_bars[asset]) + 14_400_000 - 1),
                "metric_4h_bars": len(metric_bars[asset]),
            }
            for asset, series in bars.items()
        },
        "execution": {
            "signal": "closed 4h metric snapshot and trailing normalization",
            "fill": "next 4h open",
            "base_fee_bps": float(BASE_FEE_BPS),
            "base_slippage_bps": float(BASE_SLIPPAGE_BPS),
            "stress_fee_bps": float(STRESS_FEE_BPS),
            "stress_slippage_bps": float(STRESS_SLIPPAGE_BPS),
            "historical_funding": True,
            "trading_integration": False,
        },
        "selection": {
            "candidate_count": len(candidates),
            "development_eligible_count": len(eligible),
            "shortlist_size": len(shortlist),
            "hybrid_eligible_count": len(hybrid_rows),
            "minimum_development_target_rate": float(MIN_DEVELOPMENT_TARGET_RATE),
            "periods": {
                "discovery": [_timestamp(DISCOVERY[0]), _timestamp(DISCOVERY[1])],
                "validation": [_timestamp(VALIDATION[0]), _timestamp(VALIDATION[1])],
                "confirmation": [_timestamp(CONFIRMATION[0]), _timestamp(CONFIRMATION[1])],
            },
            "confirmation_used_for_selection": False,
            "selected": _selected_payload(selected),
        },
        "confirmation": confirmation.as_dict(include_daily=True) if confirmation else None,
        "stress_confirmation": stress.as_dict() if stress else None,
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "minimum_target_month_rate": float(MIN_TARGET_MONTH_RATE),
            "achieved": achieved,
        },
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The metric hybrid met reused confirmation gates; fresh forward evidence is "
                "required."
                if achieved
                else "No hybrid met the two-segment development target-month consistency gate."
                if selected is None
                else "The development-selected market-metric hybrid reached fewer than half of "
                "the 2026 months at the 15% target, despite positive base and stressed "
                "total returns."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "Binance metric archives start in 2021 for BTC and December 2021 for ETH.",
            "Incomplete 5m metric rows are skipped rather than imputed.",
            "The hybrid uses fixed-capital sleeves and does not model shared-margin liquidation.",
            "Open interest and account ratios are exchange-reported aggregates, not auditable "
            "positions.",
        ],
    }


def _selected_payload(selected: dict[str, Any] | None) -> dict[str, Any] | None:
    if selected is None:
        return None
    return {
        "metric": selected["metric"]["candidate"].as_dict(),
        "anchor_weight": float(selected["anchor_weight"]),
        "metric_weight": float(Decimal("1") - selected["anchor_weight"]),
        "outer_leverage": float(selected["leverage"]),
        "discovery": selected["results"]["discovery"].as_dict(),
        "validation": selected["results"]["validation"].as_dict(),
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only Binance futures market-metric factor study.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Development-eligible metric candidates: "
        f"`{payload['selection']['development_eligible_count']}` / "
        f"`{payload['selection']['candidate_count']}`.",
    ]
    if selected:
        metric = selected["metric"]
        lines.extend(
            [
                f"Selected `{metric['id']}` with `{selected['anchor_weight']:.0%}` anchor, "
                f"`{selected['metric_weight']:.0%}` metric sleeve, and "
                f"`{selected['outer_leverage']:.2f}x` outer leverage.",
                "",
                "| Split | Return | Max DD | 15% months |",
                "|---|---:|---:|---:|",
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
        f"{reached}/{len(result['monthly_returns'])} |"
    )


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
