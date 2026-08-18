#!/usr/bin/env python3
"""Replay the ex-post monthly-return boundary and its development-risk audit."""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from dataclasses import replace
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
    _candidate,
    _evaluate_candidate,
    _event_candidate_library,
    _period,
    _timestamp,
)
from train_walk_forward_factor import (  # noqa: E402
    ANCHOR_ALLOCATIONS,
    ANCHOR_LEVERAGE,
    _anchor_context,
    _evaluate_anchor,
)

from mastermind_tick.bar_research import (  # noqa: E402
    ResearchBar,
    aggregate_bars,
    funding_by_bar,
    macd_targets,
    rsi_reversion_targets,
)
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import (  # noqa: E402
    MonthlyRiskConfig,
    VolatilityTargetConfig,
    evaluate_monthly_risk_overlay,
    evaluate_volatility_target,
)
from mastermind_tick.factor_portfolio import (  # noqa: E402
    DailyReturns,
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
)

ASSETS = ("btc_perp", "eth_perp")
TARGET_MONTHLY_RETURN = Decimal("0.15")
BASE_OVERLAY_TURNOVER_BPS = Decimal("7")
STRESS_OVERLAY_TURNOVER_BPS = Decimal("15")
EX_POST_WEIGHTS = {
    "frozen_four_factor_anchor": Decimal("0.10"),
    "eth_daily_macd": Decimal("0.80"),
    "eth_hourly_rsi": Decimal("0.05"),
    "btc_shock_reversal": Decimal("0.05"),
}
EX_POST_LEVERAGE = Decimal("6")
EX_POST_MONTHLY_LOSS_LIMIT = Decimal("0.25")
MONTHLY_PROFIT_LOCK = Decimal("0.15")
VOLATILITY_CONFIG = VolatilityTargetConfig(
    lookback_days=40,
    target_daily_volatility=Decimal("0.02"),
    minimum_exposure=Decimal("1"),
    maximum_exposure=Decimal("3"),
    rebalance_frequency="daily",
    turnover_bps=BASE_OVERLAY_TURNOVER_BPS,
)
VOLATILITY_MONTHLY_LOSS_LIMIT = Decimal("0.10")
SHOCK_ID = (
    "event-btc_perp-to-btc_perp-reversal-15d-threshold-1p5-"
    "hold-12x4h-none-long_only"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/monthly_target_feasibility/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id for reproducible refreshes")
    args = parser.parse_args()

    print("loading aligned BTC/ETH history", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    contexts = _contexts(loaded)

    print("replaying the explicitly ex-post 2026 boundary", flush=True)
    confirmation = {
        cost: _confirmation_replay(contexts, stress=stress)
        for cost, stress in (("base", False), ("stress", True))
    }

    print("auditing the fixed composition on 2021-2025 development data", flush=True)
    development = _development_audit(contexts)
    payload = _report(loaded, contexts, confirmation, development)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"monthly-target-feasibility-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
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


def _contexts(loaded: dict[str, tuple[list[ResearchBar], list[Any]]]) -> dict[str, Any]:
    bars_4h = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    anchor = _anchor_context(bars_4h, loaded)

    eth_daily = aggregate_bars(loaded["eth_perp"][0], 1440)
    eth_daily_funding = funding_by_bar(eth_daily, loaded["eth_perp"][1])
    macd = _candidate(
        "eth_perp",
        "macd",
        1440,
        "long_short",
        {"fast": 10, "slow": 30, "signal": 9},
        eth_daily,
        eth_daily_funding,
        macd_targets(eth_daily, 10, 30, 9, "long_short"),
    )

    eth_hourly = aggregate_bars(loaded["eth_perp"][0], 60)
    eth_hourly_funding = funding_by_bar(eth_hourly, loaded["eth_perp"][1])
    rsi = _candidate(
        "eth_perp",
        "rsi_reversion",
        60,
        "long_only",
        {"period": 14, "lower": 30, "upper": 70},
        eth_hourly,
        eth_hourly_funding,
        rsi_reversion_targets(eth_hourly, 14, 30, 70, "long_only"),
    )

    events = {
        candidate.id: candidate
        for candidate in _event_candidate_library(
            bars_4h["btc_perp"],
            bars_4h["eth_perp"],
            loaded["btc_perp"][1],
            loaded["eth_perp"][1],
        )
    }
    return {
        "bars_4h": bars_4h,
        "anchor": anchor,
        "macd": macd,
        "rsi": rsi,
        "shock": events[SHOCK_ID],
    }


def _component_returns(
    contexts: dict[str, Any],
    period: tuple[int, int],
    *,
    stress: bool,
) -> dict[str, DailyReturns]:
    fee = STRESS_FEE_BPS if stress else BASE_FEE_BPS
    slippage = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    results = {
        "frozen_four_factor_anchor": _evaluate_anchor(
            contexts["anchor"], period, stress=stress
        ),
        "eth_daily_macd": _evaluate_candidate(
            contexts["macd"], period, fee_bps=fee, slippage_bps=slippage
        ),
        "eth_hourly_rsi": _evaluate_candidate(
            contexts["rsi"], period, fee_bps=fee, slippage_bps=slippage
        ),
        "btc_shock_reversal": _evaluate_candidate(
            contexts["shock"], period, fee_bps=fee, slippage_bps=slippage
        ),
    }
    return {name: decimal_returns(result.daily_returns) for name, result in results.items()}


def _confirmation_replay(contexts: dict[str, Any], *, stress: bool) -> dict[str, Any]:
    turnover = STRESS_OVERLAY_TURNOVER_BPS if stress else BASE_OVERLAY_TURNOVER_BPS
    components = _component_returns(contexts, CONFIRMATION, stress=stress)
    composite = evaluate_static_portfolio(components, EX_POST_WEIGHTS)
    boundary = evaluate_monthly_risk_overlay(
        composite.daily_returns,
        MonthlyRiskConfig(
            leverage=EX_POST_LEVERAGE,
            loss_limit=EX_POST_MONTHLY_LOSS_LIMIT,
            profit_target=MONTHLY_PROFIT_LOCK,
            turnover_bps=turnover,
        ),
    )
    volatility = evaluate_volatility_target(
        composite.daily_returns,
        replace(VOLATILITY_CONFIG, turnover_bps=turnover),
    )
    controlled = evaluate_monthly_risk_overlay(
        volatility.daily_returns,
        MonthlyRiskConfig(
            leverage=Decimal("1"),
            loss_limit=VOLATILITY_MONTHLY_LOSS_LIMIT,
            profit_target=MONTHLY_PROFIT_LOCK,
            turnover_bps=turnover,
        ),
    )
    confirmation_end = datetime.fromtimestamp(CONFIRMATION[1] / 1000, UTC).date()
    return {
        "ex_post_boundary": _result_summary(boundary, confirmation_end),
        "development_risk_controlled": _result_summary(controlled, confirmation_end),
    }


def _development_audit(contexts: dict[str, Any]) -> dict[str, Any]:
    periods = {"discovery_2021_2023": DISCOVERY, "validation_2024_2025": VALIDATION}
    components = {
        name: _component_returns(contexts, period, stress=False)
        for name, period in periods.items()
    }
    selected = _development_results(components, EX_POST_WEIGHTS, EX_POST_LEVERAGE)
    controlled = _development_volatility_results(components)
    weight_grid = tuple(_positive_weight_grid())
    leverages = tuple(Decimal(value) for value in range(2, 9))
    profitable_both = 0
    risk_eligible = 0
    for weights in weight_grid:
        composites = {
            name: evaluate_static_portfolio(curves, weights)
            for name, curves in components.items()
        }
        for leverage in leverages:
            results = {
                name: evaluate_monthly_risk_overlay(
                    composite.daily_returns,
                    MonthlyRiskConfig(
                        leverage=leverage,
                        loss_limit=EX_POST_MONTHLY_LOSS_LIMIT,
                        profit_target=MONTHLY_PROFIT_LOCK,
                        turnover_bps=BASE_OVERLAY_TURNOVER_BPS,
                    ),
                )
                for name, composite in composites.items()
            }
            if all(result.net_return > 0 for result in results.values()):
                profitable_both += 1
            if all(
                result.net_return > 0
                and result.max_drawdown >= Decimal("-0.35")
                and not result.bankrupt
                for result in results.values()
            ):
                risk_eligible += 1
    return {
        "protocol": {
            "weights": "all four sleeves positive; 5 percentage-point grid; sum to 100%",
            "outer_leverage": [float(value) for value in leverages],
            "monthly_loss_limit": float(EX_POST_MONTHLY_LOSS_LIMIT),
            "monthly_profit_lock": float(MONTHLY_PROFIT_LOCK),
            "maximum_allowed_daily_close_drawdown_each_split": -0.35,
            "costs": "base component costs plus 7 bps overlay turnover",
        },
        "weight_configuration_count": len(weight_grid),
        "evaluated_configuration_count": len(weight_grid) * len(leverages),
        "profitable_in_both_splits": profitable_both,
        "risk_eligible_in_both_splits": risk_eligible,
        "ex_post_configuration": selected,
        "volatility_controlled_configuration": controlled,
    }


def _development_results(
    components: dict[str, dict[str, DailyReturns]],
    weights: dict[str, Decimal],
    leverage: Decimal,
) -> dict[str, Any]:
    results = {}
    for name, curves in components.items():
        composite = evaluate_static_portfolio(curves, weights)
        result = evaluate_monthly_risk_overlay(
            composite.daily_returns,
            MonthlyRiskConfig(
                leverage=leverage,
                loss_limit=EX_POST_MONTHLY_LOSS_LIMIT,
                profit_target=MONTHLY_PROFIT_LOCK,
                turnover_bps=BASE_OVERLAY_TURNOVER_BPS,
            ),
        )
        results[name] = {
            "net_return": float(result.net_return),
            "max_drawdown": float(result.max_drawdown),
            "bankrupt": result.bankrupt,
            "passes_return_and_drawdown_gate": (
                result.net_return > 0
                and result.max_drawdown >= Decimal("-0.35")
                and not result.bankrupt
            ),
        }
    return results


def _development_volatility_results(
    components: dict[str, dict[str, DailyReturns]],
) -> dict[str, Any]:
    results = {}
    for name, curves in components.items():
        composite = evaluate_static_portfolio(curves, EX_POST_WEIGHTS)
        volatility = evaluate_volatility_target(composite.daily_returns, VOLATILITY_CONFIG)
        result = evaluate_monthly_risk_overlay(
            volatility.daily_returns,
            MonthlyRiskConfig(
                leverage=Decimal("1"),
                loss_limit=VOLATILITY_MONTHLY_LOSS_LIMIT,
                profit_target=MONTHLY_PROFIT_LOCK,
                turnover_bps=BASE_OVERLAY_TURNOVER_BPS,
            ),
        )
        results[name] = {
            "net_return": float(result.net_return),
            "max_drawdown": float(result.max_drawdown),
            "bankrupt": result.bankrupt,
            "passes_return_and_drawdown_gate": (
                result.net_return > 0
                and result.max_drawdown >= Decimal("-0.35")
                and not result.bankrupt
            ),
        }
    return results


def _positive_weight_grid() -> list[dict[str, Decimal]]:
    names = tuple(EX_POST_WEIGHTS)
    step = Decimal("0.05")
    result = []
    for first in range(1, 18):
        for second in range(1, 18):
            for third in range(1, 18):
                values = [step * Decimal(value) for value in (first, second, third)]
                fourth = Decimal("1") - sum(values, Decimal("0"))
                if fourth >= step:
                    result.append(dict(zip(names, (*values, fourth), strict=True)))
    return result


def _result_summary(result: PortfolioResult, period_end: date) -> dict[str, Any]:
    monthly = tuple((label, value) for label, value in result.monthly_returns)
    complete = tuple(
        (label, value) for label, value in monthly if _month_is_complete(label, period_end)
    )
    partial = tuple(
        (label, value) for label, value in monthly if not _month_is_complete(label, period_end)
    )
    complete_return = _compound(value for _label, value in complete)
    return {
        "net_return_including_partial_month": float(result.net_return),
        "complete_month_net_return": float(complete_return),
        "max_drawdown_daily_close": float(result.max_drawdown),
        "bankrupt": result.bankrupt,
        "complete_months": [
            {"label": label, "return": float(value), "target_met": value >= TARGET_MONTHLY_RETURN}
            for label, value in complete
        ],
        "partial_months": [
            {"label": label, "return": float(value), "target_met": None}
            for label, value in partial
        ],
        "complete_month_count": len(complete),
        "complete_target_month_count": sum(
            value >= TARGET_MONTHLY_RETURN for _label, value in complete
        ),
        "strict_goal_met": bool(complete) and all(
            value >= TARGET_MONTHLY_RETURN for _label, value in complete
        ),
    }


def _month_is_complete(label: str, period_end: date) -> bool:
    year, month = (int(value) for value in label.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day) <= period_end


def _compound(values: Any) -> Decimal:
    equity = Decimal("1")
    for value in values:
        equity *= Decimal("1") + value
    return equity - Decimal("1")


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    contexts: dict[str, Any],
    confirmation: dict[str, Any],
    development: dict[str, Any],
) -> dict[str, Any]:
    base_boundary = confirmation["base"]["ex_post_boundary"]
    stress_boundary = confirmation["stress"]["ex_post_boundary"]
    base_controlled = confirmation["base"]["development_risk_controlled"]
    stress_controlled = confirmation["stress"]["development_risk_controlled"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "study": "strict +15% complete-month target feasibility boundary",
        "data": {
            asset: {
                "first_bar": _timestamp(rows[0][0].start_ms),
                "last_bar": _timestamp(rows[0][-1].end_ms),
                "source_bar_count": len(rows[0]),
            }
            for asset, rows in loaded.items()
        },
        "periods": {
            "development_discovery": _period(DISCOVERY),
            "development_validation": _period(VALIDATION),
            "reused_confirmation": _period(CONFIRMATION),
            "strict_complete_confirmation_months": "2026-01 through 2026-07",
            "partial_month_excluded_from_goal": "2026-08",
        },
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "rule": "every complete confirmation month must return at least +15%",
            "partial_months_count_toward_goal": False,
        },
        "execution": {
            "signal_timing": "causal signals on closed component bars",
            "fill_timing": "next component bar open",
            "base_costs": {
                "fee_bps_per_fill": float(BASE_FEE_BPS),
                "slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
                "overlay_turnover_bps": float(BASE_OVERLAY_TURNOVER_BPS),
            },
            "stress_costs": {
                "fee_bps_per_fill": float(STRESS_FEE_BPS),
                "slippage_bps_per_fill": float(STRESS_SLIPPAGE_BPS),
                "overlay_turnover_bps": float(STRESS_OVERLAY_TURNOVER_BPS),
            },
            "funding": "historical instrument funding while positioned",
            "portfolio_model": "fixed initial sleeve capital; no implicit daily rebalancing",
            "liquidation_modeled": False,
        },
        "ex_post_boundary": {
            "selection_warning": (
                "components, weights, leverage, and locks were chosen after inspecting 2026; "
                "this is a feasibility upper bound, not a tradable candidate"
            ),
            "weights": {name: float(value) for name, value in EX_POST_WEIGHTS.items()},
            "anchor_internal": {
                "weights": {name: float(value) for name, value in ANCHOR_ALLOCATIONS.items()},
                "leverage": float(ANCHOR_LEVERAGE),
            },
            "components": {
                "eth_daily_macd": "MACD(10,30,9), long/short, daily bars",
                "eth_hourly_rsi": "RSI(14,30,70), long-only, 60-minute bars",
                "btc_shock_reversal": (
                    "15-day causal normalization, 1.5 threshold, 12x4h hold, long-only"
                ),
            },
            "outer_leverage": float(EX_POST_LEVERAGE),
            "monthly_loss_limit": float(EX_POST_MONTHLY_LOSS_LIMIT),
            "monthly_profit_lock": float(MONTHLY_PROFIT_LOCK),
            "base": base_boundary,
            "stress": stress_boundary,
        },
        "development_risk_controlled": {
            "configuration": {
                "weights": {name: float(value) for name, value in EX_POST_WEIGHTS.items()},
                "volatility_lookback_days": VOLATILITY_CONFIG.lookback_days,
                "target_daily_volatility": float(VOLATILITY_CONFIG.target_daily_volatility),
                "minimum_exposure": float(VOLATILITY_CONFIG.minimum_exposure),
                "maximum_exposure": float(VOLATILITY_CONFIG.maximum_exposure),
                "rebalance_frequency": VOLATILITY_CONFIG.rebalance_frequency,
                "monthly_loss_limit": float(VOLATILITY_MONTHLY_LOSS_LIMIT),
                "monthly_profit_lock": float(MONTHLY_PROFIT_LOCK),
            },
            "base": base_controlled,
            "stress": stress_controlled,
        },
        "development_reverse_audit": development,
        "decision": {
            "status": "rejected_protocol_selection_bias_and_development_failure",
            "approved_for_trading": False,
            "goal_achieved": False,
            "reason": (
                "The 7/7 boundary is selected on reused 2026 data. No positive 5%-weight-grid "
                "configuration at 2x-8x passed return and -35% drawdown gates in both development "
                "splits, while the causal risk-controlled version reached only 3/7 complete months."
            ),
            "boundary_goal_met_base": base_boundary["strict_goal_met"],
            "boundary_goal_met_stress": stress_boundary["strict_goal_met"],
            "risk_controlled_goal_met_base": base_controlled["strict_goal_met"],
            "risk_controlled_goal_met_stress": stress_controlled["strict_goal_met"],
        },
    }


def _markdown(payload: dict[str, Any]) -> str:
    boundary = payload["ex_post_boundary"]
    controlled = payload["development_risk_controlled"]
    audit = payload["development_reverse_audit"]
    monthly_labels = [row["label"] for row in boundary["base"]["complete_months"]]
    partial_labels = [row["label"] for row in boundary["base"]["partial_months"]]
    rows = [
        "# Strict +15% Monthly Target Feasibility Audit",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Decision",
        "",
        "**Rejected. The strict goal is not achieved by a valid research protocol and this is not "
        "approved for trading.**",
        "",
        "A formula can be made to clear all seven complete 2026 months after inspecting those same "
        "months. That result is an ex-post feasibility boundary, not out-of-sample evidence. Once "
        "the composition is constrained by causal volatility sizing that survives development risk "
        "controls, only 3/7 complete months reach +15% under both cost models.",
        "",
        "## Monthly Results",
        "",
        "| Month | Boundary base | Boundary stress | Risk-controlled base | "
        "Risk-controlled stress | Goal audit |",
        "|---|---:|---:|---:|---:|---|",
    ]
    series = {
        "boundary_base": {
            row["label"]: row["return"] for row in boundary["base"]["complete_months"]
        },
        "boundary_stress": {
            row["label"]: row["return"] for row in boundary["stress"]["complete_months"]
        },
        "controlled_base": {
            row["label"]: row["return"] for row in controlled["base"]["complete_months"]
        },
        "controlled_stress": {
            row["label"]: row["return"] for row in controlled["stress"]["complete_months"]
        },
    }
    for label in monthly_labels:
        rows.append(
            f"| {label} | {_pct(series['boundary_base'][label])} | "
            f"{_pct(series['boundary_stress'][label])} | "
            f"{_pct(series['controlled_base'][label])} | "
            f"{_pct(series['controlled_stress'][label])} | complete |"
        )
    for label in partial_labels:
        partial = {
            key: next(row["return"] for row in value["partial_months"] if row["label"] == label)
            for key, value in (
                ("boundary_base", boundary["base"]),
                ("boundary_stress", boundary["stress"]),
                ("controlled_base", controlled["base"]),
                ("controlled_stress", controlled["stress"]),
            )
        }
        rows.append(
            f"| {label} | {_pct(partial['boundary_base'])} | "
            f"{_pct(partial['boundary_stress'])} | {_pct(partial['controlled_base'])} | "
            f"{_pct(partial['controlled_stress'])} | **partial; excluded** |"
        )
    rows.extend(
        [
            "",
            "| Replay | Complete target months | Total incl. partial | Daily-close max DD |",
            "|---|---:|---:|---:|",
            _summary_row("Ex-post boundary, base", boundary["base"]),
            _summary_row("Ex-post boundary, stress", boundary["stress"]),
            _summary_row("Risk-controlled, base", controlled["base"]),
            _summary_row("Risk-controlled, stress", controlled["stress"]),
            "",
            "## Ex-Post Boundary",
            "",
            "- 10% frozen four-factor anchor, 80% ETH daily MACD(10,30,9) long/short, "
            "5% ETH 60m RSI(14,30,70) long-only, and 5% BTC 15-day shock reversal long-only.",
            "- 6x outer leverage, 25% monthly loss lock, and 15% monthly profit lock.",
            "- Base costs are 5 bps fee + 2 bps slippage per fill and 7 bps overlay turnover. "
            "Stress costs are 10 + 5 bps and 15 bps overlay turnover.",
            "",
            "The sleeve choices and weights were found after inspecting 2026. The apparent 7/7 "
            "success therefore cannot be used as evidence that the same rule will work "
            "prospectively.",
            "",
            "## Reverse Audit",
            "",
            f"The audit scanned `{audit['weight_configuration_count']:,}` strictly positive "
            "5%-step weight combinations and 2x through 8x leverage: "
            f"`{audit['evaluated_configuration_count']:,}` configurations total.",
            "",
            f"- Profitable in both 2021-2023 and 2024-2025: "
            f"`{audit['profitable_in_both_splits']:,}`.",
            "- Profitable with daily-close drawdown no worse than -35% in both splits: "
            f"`{audit['risk_eligible_in_both_splits']:,}`.",
            "",
            "The ex-post 7/7 weights returned "
            f"`{_pct(audit['ex_post_configuration']['discovery_2021_2023']['net_return'])}` with "
            f"`{_pct(audit['ex_post_configuration']['discovery_2021_2023']['max_drawdown'])}` "
            "drawdown in 2021-2023, then "
            f"`{_pct(audit['ex_post_configuration']['validation_2024_2025']['net_return'])}` with "
            f"`{_pct(audit['ex_post_configuration']['validation_2024_2025']['max_drawdown'])}` "
            "drawdown in 2024-2025.",
            "",
            "## Causal Risk Control",
            "",
            "The same fixed composition was constrained with a trailing 40-day daily RMS, a 2% "
            "daily volatility target, 1x-3x exposure, a 10% monthly loss lock, and the same 15% "
            "profit lock. It passes the positive-return and -35% drawdown gates in both "
            "development splits. In reused confirmation it keeps modeled daily-close drawdown near "
            "14%, but removes the claimed monthly consistency: only 3/7 complete months clear +15% "
            "in either cost model.",
            "",
            "## Limitations",
            "",
            "- Confirmation year 2026 was reused throughout prior research and is not a fresh "
            "holdout.",
            "- Daily-close drawdown can miss intraday liquidation risk; liquidation is not "
            "modeled.",
            "- August is incomplete and is shown only as a partial diagnostic.",
            "- Achieving a target in every calendar month is not treated as a relaxed coverage "
            "goal.",
            "",
        ]
    )
    return "\n".join(rows)


def _summary_row(label: str, result: dict[str, Any]) -> str:
    return (
        f"| {label} | {result['complete_target_month_count']}/"
        f"{result['complete_month_count']} | "
        f"{_pct(result['net_return_including_partial_month'])} | "
        f"{_pct(result['max_drawdown_daily_close'])} |"
    )


def _pct(value: float) -> str:
    return f"{value:+.2%}"


if __name__ == "__main__":
    main()
