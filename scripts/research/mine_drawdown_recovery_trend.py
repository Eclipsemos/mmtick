#!/usr/bin/env python3
"""Test development-selected MACD sleeves activated after causal monthly drawdown."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_btc_order_flow import _load_flow_cache, _periods  # noqa: E402
from mine_defensive_factor_portfolio import (  # noqa: E402
    _development_eligible,
    _period_returns,
    _strict_count,
)
from mine_monthly_robust_ensemble import (  # noqa: E402
    _blend_returns,
    _result_payload,
    _risk_score,
)
from mine_monthly_target_regime_router import (  # noqa: E402
    ASSETS,
    BASE_OVERLAY_TURNOVER_BPS,
    COMPLETE_CONFIRMATION_END,
    STRESS_OVERLAY_TURNOVER_BPS,
    TARGET_MONTHLY_RETURN,
    _evaluate_macd,
    _macd_candidates,
    _period_payload,
    _state_curves,
    _timestamp,
)
from mine_order_flow_complement import _candidate_replays  # noqa: E402

from mastermind_tick.bar_research import ResearchBar, aggregate_bars, funding_by_bar  # noqa: E402
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import (  # noqa: E402
    MonthlyRiskConfig,
    evaluate_monthly_risk_overlay,
)
from mastermind_tick.factor_portfolio import (  # noqa: E402
    DailyReturns,
    PortfolioResult,
    monthly_returns,
)
from mastermind_tick.order_flow import (  # noqa: E402
    candidate_library,
    causal_flow_features,
)

BASELINE_REPORT_ID = "monthly-robust-ensemble-20260815"
BASELINE_FLOW_IDS = (
    "tick_rule_imbalance_revert-window-42-long_only-threshold-0p75-ema-4-hold-6-"
    "cooldown-0-confirm-2",
    "reported_imbalance_follow-window-126-long_only-threshold-1p25-ema-4-hold-6-"
    "cooldown-0-confirm-2",
    "reported_imbalance_follow-window-126-long_short-threshold-1p25-ema-4-hold-6-"
    "cooldown-6-confirm-1",
    "tick_rule_absorption-window-42-long_only-threshold-1p25-ema-4-hold-1-cooldown-6-confirm-1",
)
BASELINE_WEIGHTS = {
    "frozen_state": Decimal("0.1275"),
    BASELINE_FLOW_IDS[0]: Decimal("0.3825"),
    BASELINE_FLOW_IDS[1]: Decimal("0.085"),
    BASELINE_FLOW_IDS[2]: Decimal("0.255"),
    BASELINE_FLOW_IDS[3]: Decimal("0.15"),
}
BASELINE_LEVERAGE = Decimal("8")
BASELINE_LOSS_LIMIT = Decimal("0.20")
BASELINE_PROFIT_TARGET = Decimal("0.16")
PAIR_SOURCE_SHORTLIST_SIZE = 40
PAIR_SHORTLIST_SIZE = 60
PAIR_WEIGHTS = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75"))
TRIGGERS = tuple(Decimal(value) for value in ("-0.01", "-0.025", "-0.05", "-0.075", "-0.10"))
TREND_WEIGHTS = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75", "1"))
LEVERAGES = tuple(Decimal(value) for value in ("1", "1.5", "2", "3", "4", "5", "6", "8"))
LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25"))
PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18"))


@dataclass(frozen=True)
class RecoveryConfig:
    sleeve_id: str
    trigger: Decimal
    trend_weight: Decimal
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"recovery-{self.sleeve_id}-trigger{self.trigger}-weight{self.trend_weight}-"
            f"lev{self.leverage}-loss{self.loss_limit}-profit{self.profit_target}"
        )

    def risk(self, turnover_bps: Decimal) -> MonthlyRiskConfig:
        return MonthlyRiskConfig(
            self.leverage,
            self.loss_limit,
            self.profit_target,
            turnover_bps,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sleeve_id": self.sleeve_id,
            "monthly_drawdown_trigger": float(self.trigger),
            "active_trend_weight": float(self.trend_weight),
            "active_baseline_weight": float(Decimal("1") - self.trend_weight),
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
        default=Path("reports/experiments/drawdown_recovery_trend/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    print("reconstructing the frozen monthly-robust baseline", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state_curves = _state_curves(loaded, args.metrics_dir)
    periods = _periods()
    flow = _load_flow_cache(args.flow_cache)
    if flow is None:
        raise FileNotFoundError(f"missing version-3 order-flow cache: {args.flow_cache}")
    baseline = _baseline_curves(loaded, state_curves, flow, periods)
    baseline_results = _baseline_development_results(baseline, periods)
    loss_months = _loss_months(baseline_results)
    print(f"development loss months: {loss_months}", flush=True)

    print("replaying and conditionally screening the predefined MACD library", flush=True)
    macd_rows = [_evaluate_macd(candidate) for candidate in _macd_candidates(loaded)]
    eligible_singles = _conditional_singles(macd_rows, loss_months)
    print(f"building pairs from the top {PAIR_SOURCE_SHORTLIST_SIZE} trends", flush=True)
    pair_rows = _conditional_pairs(eligible_singles[:PAIR_SOURCE_SHORTLIST_SIZE], loss_months)
    selected_pairs = pair_rows[:PAIR_SHORTLIST_SIZE]
    sleeves = [*eligible_singles, *selected_pairs]

    print(f"searching causal recovery controls for {len(sleeves)} sleeves", flush=True)
    eligible_configs = _search_controls(baseline, sleeves, periods)
    print(f"auditing {len(eligible_configs):,} development-eligible controls", flush=True)
    audit = _confirmation_audit(eligible_configs, periods)

    payload = _report(
        loaded,
        flow,
        baseline_results,
        loss_months,
        macd_rows,
        eligible_singles,
        pair_rows,
        selected_pairs,
        eligible_configs,
        audit,
        periods,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"drawdown-recovery-trend-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _baseline_curves(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    state_curves: dict[str, DailyReturns],
    flow: dict[int, Any],
    periods: dict[str, tuple[int, int]],
) -> dict[str, DailyReturns]:
    bars = aggregate_bars(loaded["btc_perp"][0], 240)
    funding = funding_by_bar(bars, loaded["btc_perp"][1])
    candidates = tuple(
        candidate for candidate in candidate_library() if candidate.id in BASELINE_FLOW_IDS
    )
    if len(candidates) != len(BASELINE_FLOW_IDS):
        raise ValueError("frozen baseline order-flow candidates are missing")
    features = {
        window: causal_flow_features(bars, flow, window)
        for window in {candidate.window for candidate in candidates}
    }
    rows = _candidate_replays(candidates, features, bars, funding, periods)
    replays = {row["candidate"].id: row["returns"] for row in rows}
    if set(replays) != set(BASELINE_FLOW_IDS):
        raise ValueError("frozen baseline order-flow replay failed development eligibility")
    return {
        cost: _weighted_returns(
            {
                "frozen_state": state_curves[cost],
                **{candidate_id: replays[candidate_id][cost] for candidate_id in BASELINE_FLOW_IDS},
            },
            BASELINE_WEIGHTS,
        )
        for cost in ("base", "stress")
    }


def _weighted_returns(curves: dict[str, DailyReturns], weights: dict[str, Decimal]) -> DailyReturns:
    if set(curves) != set(weights) or sum(weights.values(), Decimal("0")) != Decimal("1"):
        raise ValueError("weighted return inputs are invalid")
    by_name = {name: dict(rows) for name, rows in curves.items()}
    labels = set.intersection(*(set(rows) for rows in by_name.values()))
    return tuple(
        (
            label,
            sum((weights[name] * by_name[name][label] for name in weights), Decimal("0")),
        )
        for label in sorted(labels)
    )


def _baseline_development_results(
    baseline: dict[str, DailyReturns], periods: dict[str, tuple[int, int]]
) -> dict[str, dict[str, PortfolioResult]]:
    return {
        cost: {
            split: evaluate_monthly_risk_overlay(
                _period_returns(values, periods[split]),
                MonthlyRiskConfig(
                    BASELINE_LEVERAGE,
                    BASELINE_LOSS_LIMIT,
                    BASELINE_PROFIT_TARGET,
                    BASE_OVERLAY_TURNOVER_BPS if cost == "base" else STRESS_OVERLAY_TURNOVER_BPS,
                ),
            )
            for split in ("train", "validation")
        }
        for cost, values in baseline.items()
    }


def _loss_months(
    results: dict[str, dict[str, PortfolioResult]],
) -> dict[str, dict[str, tuple[str, ...]]]:
    return {
        cost: {
            split: tuple(label for label, value in result.monthly_returns if value < 0)
            for split, result in values.items()
        }
        for cost, values in results.items()
    }


def _conditional_singles(
    macd_rows: list[dict[str, Any]],
    loss_months: dict[str, dict[str, tuple[str, ...]]],
) -> list[dict[str, Any]]:
    rows = []
    for row in macd_rows:
        monthly = {cost: dict(monthly_returns(values)) for cost, values in row["returns"].items()}
        values = _conditional_values(monthly, loss_months)
        if values and min(values) > 0:
            rows.append(
                {
                    "id": row["candidate"].id,
                    "kind": "single",
                    "metadata": row["candidate"].as_dict(),
                    "returns": row["returns"],
                    "conditional_values": values,
                    "score": (min(values), sum(values, Decimal("0"))),
                }
            )
    return sorted(rows, key=lambda item: item["score"], reverse=True)


def _conditional_pairs(
    singles: list[dict[str, Any]],
    loss_months: dict[str, dict[str, tuple[str, ...]]],
) -> list[dict[str, Any]]:
    rows = []
    for left, right in combinations(singles, 2):
        for left_weight in PAIR_WEIGHTS:
            right_weight = Decimal("1") - left_weight
            returns = {
                cost: _blend_returns(left["returns"][cost], right["returns"][cost], right_weight)
                for cost in ("base", "stress")
            }
            monthly = {cost: dict(monthly_returns(values)) for cost, values in returns.items()}
            values = _conditional_values(monthly, loss_months)
            if values and min(values) > 0:
                pair_id = f"trend-pair-{left['id']}-weight{left_weight}-{right['id']}"
                rows.append(
                    {
                        "id": pair_id,
                        "kind": "pair",
                        "metadata": {
                            "id": pair_id,
                            "left": left["id"],
                            "left_weight": float(left_weight),
                            "right": right["id"],
                            "right_weight": float(right_weight),
                        },
                        "returns": returns,
                        "conditional_values": values,
                        "score": (min(values), sum(values, Decimal("0"))),
                    }
                )
    return sorted(rows, key=lambda item: item["score"], reverse=True)


def _conditional_values(
    monthly: dict[str, dict[str, Decimal]],
    loss_months: dict[str, dict[str, tuple[str, ...]]],
) -> tuple[Decimal, ...]:
    labels = tuple(
        (cost, split, label)
        for cost, splits in loss_months.items()
        for split, months in splits.items()
        for label in months
    )
    if not labels or any(label not in monthly[cost] for cost, _split, label in labels):
        return ()
    return tuple(monthly[cost][label] for cost, _split, label in labels)


def _recovery_returns(
    baseline: DailyReturns,
    trend: DailyReturns,
    trigger: Decimal,
    trend_weight: Decimal,
    turnover_bps: Decimal,
) -> DailyReturns:
    if trigger >= 0 or not Decimal("0") <= trend_weight <= Decimal("1"):
        raise ValueError("recovery trigger or weight is invalid")
    trend_by_label = dict(trend)
    result = []
    month = None
    baseline_equity = Decimal("1")
    active = False
    previous_active = False
    rate = turnover_bps / Decimal("10000")
    for label, baseline_return in baseline:
        if label not in trend_by_label:
            continue
        if label[:7] != month:
            month = label[:7]
            baseline_equity = Decimal("1")
            active = False
        if active:
            selected_return = (
                Decimal("1") - trend_weight
            ) * baseline_return + trend_weight * trend_by_label[label]
        else:
            selected_return = baseline_return
        switch_cost = trend_weight * rate if active != previous_active else Decimal("0")
        result.append((label, selected_return - switch_cost))
        previous_active = active
        baseline_equity *= Decimal("1") + baseline_return
        if not active and baseline_equity - Decimal("1") <= trigger:
            active = True
    return tuple(result)


def _search_controls(
    baseline: dict[str, DailyReturns],
    sleeves: list[dict[str, Any]],
    periods: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    rows = []
    for index, sleeve in enumerate(sleeves, start=1):
        for trigger in TRIGGERS:
            for trend_weight in TREND_WEIGHTS:
                curves = {
                    cost: _recovery_returns(
                        baseline[cost],
                        sleeve["returns"][cost],
                        trigger,
                        trend_weight,
                        BASE_OVERLAY_TURNOVER_BPS
                        if cost == "base"
                        else STRESS_OVERLAY_TURNOVER_BPS,
                    )
                    for cost in ("base", "stress")
                }
                for leverage in LEVERAGES:
                    for loss_limit in LOSS_LIMITS:
                        for profit_target in PROFIT_TARGETS:
                            config = RecoveryConfig(
                                sleeve["id"],
                                trigger,
                                trend_weight,
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
                                for cost, values in curves.items()
                            }
                            if _development_eligible(results):
                                rows.append(
                                    {
                                        "sleeve": sleeve,
                                        "config": config,
                                        "returns": curves,
                                        "results": results,
                                        "score": _risk_score(results),
                                    }
                                )
        if index % 20 == 0:
            print(f"recovery sleeve {index}/{len(sleeves)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda item: item["score"], reverse=True)


def _confirmation_audit(
    rows: list[dict[str, Any]], periods: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    audited = []
    for row in rows:
        config: RecoveryConfig = row["config"]
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
    baseline_results: dict[str, dict[str, PortfolioResult]],
    loss_months: dict[str, dict[str, tuple[str, ...]]],
    macd_rows: list[dict[str, Any]],
    singles: list[dict[str, Any]],
    pair_rows: list[dict[str, Any]],
    selected_pairs: list[dict[str, Any]],
    configs: list[dict[str, Any]],
    audit: dict[str, Any],
    periods: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    strict_count = audit["strict_pass_count"]
    best_count = (
        min(audit["best_confirmation"]["counts"].values()) if audit["best_confirmation"] else 0
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "causal monthly-drawdown recovery with development-selected MACD",
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
            "order_flow_bars": len(flow),
        },
        "protocol": {
            "train": _period_payload(periods["train"]),
            "validation": _period_payload(periods["validation"]),
            "confirmation": _period_payload(periods["confirmation"]),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "baseline_report_id": BASELINE_REPORT_ID,
            "trigger_timing": (
                "baseline cumulative return through UTC day D can activate recovery on D+1; "
                "activation persists through month end"
            ),
            "conditional_selection": (
                "MACD return must be positive under base and stress costs in every negative "
                "baseline month of both development years"
            ),
        },
        "baseline": {
            "weights": {name: float(value) for name, value in BASELINE_WEIGHTS.items()},
            "leverage": float(BASELINE_LEVERAGE),
            "monthly_loss_limit": float(BASELINE_LOSS_LIMIT),
            "monthly_profit_target": float(BASELINE_PROFIT_TARGET),
            "development_loss_months": {
                cost: {split: list(months) for split, months in values.items()}
                for cost, values in loss_months.items()
            },
            "development": {
                cost: {split: _result_payload(result) for split, result in values.items()}
                for cost, values in baseline_results.items()
            },
        },
        "search": {
            "macd_candidate_count": len(macd_rows),
            "conditional_single_count": len(singles),
            "pair_source_shortlist_size": min(PAIR_SOURCE_SHORTLIST_SIZE, len(singles)),
            "conditional_pair_count": len(pair_rows),
            "pair_shortlist_size": len(selected_pairs),
            "recovery_sleeve_count": len(singles) + len(selected_pairs),
            "control_grid_per_sleeve": (
                len(TRIGGERS)
                * len(TREND_WEIGHTS)
                * len(LEVERAGES)
                * len(LOSS_LIMITS)
                * len(PROFIT_TARGETS)
            ),
            "development_risk_eligible_count": len(configs),
            "top_conditional_singles": [_sleeve_payload(row) for row in singles[:20]],
            "top_conditional_pairs": [_sleeve_payload(row) for row in selected_pairs[:20]],
            "top_development_controls": [_development_payload(row) for row in configs[:20]],
        },
        "selection": {
            "development_selected": _audit_payload(audit["development_selected"]),
        },
        "confirmation_audit": {
            "configuration_count": audit["configuration_count"],
            "strict_pass_count": strict_count,
            "best_complete_month_count": best_count,
            "best_confirmation_diagnostic": _audit_payload(audit["best_confirmation"]),
            "strict_examples": [_audit_payload(row) for row in audit["strict_examples"]],
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
                "At least one development-selected causal recovery sleeve reached +15% in all "
                "seven complete reused-confirmation months under base and stress costs; fresh "
                "forward evidence remains required."
                if strict_count
                else "No development-selected causal recovery sleeve reached +15% in all seven "
                "complete 2026 months under both base and stress costs."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The recovery hypothesis was specified after observing prior 2026 failures.",
            "Only MACD sleeves positive in all frozen-baseline development loss months are used.",
            "Allocation switches and monthly exposure changes pay explicit turnover costs.",
            "Drawdown is daily-close only; liquidation and borrowing costs are not modeled.",
        ],
    }


def _sleeve_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "metadata": row["metadata"],
        "conditional_returns": [float(value) for value in row["conditional_values"]],
        "score": [float(value) for value in row["score"]],
    }


def _development_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sleeve": _sleeve_payload(row["sleeve"]),
        "config": row["config"].as_dict(),
        "score": [float(value) for value in row["score"]],
        "development": {
            cost: {split: _result_payload(result) for split, result in values.items()}
            for cost, values in row["results"].items()
        },
    }


def _audit_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "sleeve": _sleeve_payload(row["row"]["sleeve"]),
        "config": row["row"]["config"].as_dict(),
        "counts": row["counts"],
        "development_score": [float(value) for value in row["row"]["score"]],
        "confirmation": {cost: _result_payload(result) for cost, result in row["results"].items()},
    }


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    lines = [
        f"# {payload['id']}",
        "",
        "Causal monthly-drawdown recovery using development-selected single and paired MACD",
        "sleeves around the frozen monthly-robust ensemble.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        "## Search Result",
        "",
        f"- Conditional single MACD sleeves: `{payload['search']['conditional_single_count']}`.",
        f"- Development-selected MACD pairs: `{payload['search']['pair_shortlist_size']}`.",
        f"- Development risk-eligible controls: "
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
            "# Drawdown Recovery Trend",
            "",
            "This study activates development-selected single or paired MACD sleeves one UTC",
            "day after the frozen baseline breaches a monthly drawdown threshold. Selection uses",
            "2024/2025 only; January-July 2026 is reused confirmation.",
            "",
            f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
            f"Best strict coverage: `{audit['best_complete_month_count']}/7`; base-and-stress "
            f"7/7 configurations: `{audit['strict_pass_count']}`.",
            "",
            "Reproduce from the repository root:",
            "",
            "```bash",
            ".venv/bin/python scripts/research/mine_drawdown_recovery_trend.py \\",
            "  --report-id drawdown-recovery-trend-20260815",
            "```",
            "",
        ]
    )


def _config_line(row: dict[str, Any]) -> str:
    config = row["config"]
    return (
        f"`{row['sleeve']['id']}`; trigger `{config['monthly_drawdown_trigger']:.1%}`; "
        f"trend/baseline `{config['active_trend_weight']:.0%}/"
        f"{config['active_baseline_weight']:.0%}`; leverage `{config['leverage']:.2f}x`; "
        f"monthly loss/profit locks `{config['monthly_loss_limit']:.0%}/"
        f"{config['monthly_profit_target']:.0%}`."
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
