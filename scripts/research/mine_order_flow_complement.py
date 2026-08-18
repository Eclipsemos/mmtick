#!/usr/bin/env python3
"""Test BTC order-flow factors as complements to the frozen market-state strategy."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_btc_order_flow import (  # noqa: E402
    _development_eligible as _flow_eligible,
)
from mine_btc_order_flow import (
    _load_flow_cache,
    _periods,
)
from mine_defensive_factor_portfolio import (  # noqa: E402
    DefensiveConfig,
    _audited_payload,
    _composite_returns,
    _confirmation_audit,
    _development_eligible,
    _development_score,
    _strict_count,
)
from mine_factor_portfolio import (  # noqa: E402
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
)
from mine_fast_trend_complement import _unlocked_result  # noqa: E402
from mine_monthly_target_regime_router import (  # noqa: E402
    ASSETS,
    COMPLETE_CONFIRMATION_END,
    TARGET_MONTHLY_RETURN,
    _period_payload,
    _state_curves,
    _timestamp,
)

from mastermind_tick.bar_research import (  # noqa: E402
    ResearchBar,
    aggregate_bars,
    evaluate_targets,
    funding_by_bar,
)
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import evaluate_monthly_risk_overlay  # noqa: E402
from mastermind_tick.factor_portfolio import DailyReturns, decimal_returns  # noqa: E402
from mastermind_tick.order_flow import (  # noqa: E402
    FlowCandidate,
    candidate_library,
    causal_flow_features,
    flow_targets,
)

STATE_WEIGHTS = tuple(
    Decimal(value) for value in ("0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.75", "0.9")
)
LEVERAGES = tuple(Decimal(value) for value in ("1", "1.5", "2", "3", "4", "5", "6", "8", "10"))
LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25"))
PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18"))
PAIR_WEIGHTS = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75"))
PAIR_SHORTLIST_SIZE = 60


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
        default=Path("reports/experiments/order_flow_complement/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    print("loading frozen state and cached BTC order flow", flush=True)
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

    print(f"screening {len(candidates):,} order-flow candidates on 2024/2025", flush=True)
    eligible_candidates = _candidate_replays(candidates, features, bars, funding, periods)
    print(f"searching controls for {len(eligible_candidates)} development factors", flush=True)
    replays = {row["candidate"].id: row["returns"] for row in eligible_candidates}
    eligible_configs = _search_configs(state_curves, replays, periods)
    print(f"auditing {len(eligible_configs):,} development-eligible configurations", flush=True)
    single_audit = _confirmation_audit(state_curves, replays, eligible_configs)

    print("screening two-factor order-flow portfolios on development only", flush=True)
    pair_rows = _pair_shortlist(replays, periods)
    selected_pairs = pair_rows[:PAIR_SHORTLIST_SIZE]
    pair_replays = {row["id"]: row["returns"] for row in selected_pairs}
    pair_configs = _search_configs(state_curves, pair_replays, periods)
    print(f"auditing {len(pair_configs):,} pair configurations", flush=True)
    pair_audit = _confirmation_audit(state_curves, pair_replays, pair_configs)

    payload = _report(
        loaded,
        flow,
        candidates,
        eligible_candidates,
        eligible_configs,
        single_audit,
        pair_rows,
        selected_pairs,
        pair_configs,
        pair_audit,
        periods,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"order-flow-complement-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidate_replays(
    candidates: tuple[FlowCandidate, ...],
    features: dict[int, dict[str, tuple[Decimal | None, ...]]],
    bars: list[ResearchBar],
    funding: list[list[Any]],
    periods: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    rows = []
    for index, candidate in enumerate(candidates, start=1):
        targets = flow_targets(features[candidate.window][candidate.feature], candidate)
        base = {
            name: evaluate_targets(
                bars,
                targets,
                start_ms=period[0],
                end_ms=period[1],
                funding=funding,
                fee_bps=BASE_FEE_BPS,
                slippage_bps=BASE_SLIPPAGE_BPS,
            )
            for name, period in periods.items()
        }
        if _flow_eligible(base):
            stress = {
                name: evaluate_targets(
                    bars,
                    targets,
                    start_ms=period[0],
                    end_ms=period[1],
                    funding=funding,
                    fee_bps=STRESS_FEE_BPS,
                    slippage_bps=STRESS_SLIPPAGE_BPS,
                )
                for name, period in periods.items()
            }
            rows.append(
                {
                    "candidate": candidate,
                    "base": base,
                    "stress": stress,
                    "returns": {
                        cost: tuple(
                            item
                            for name in ("train", "validation", "confirmation")
                            for item in decimal_returns(results[name].daily_returns)
                        )
                        for cost, results in (("base", base), ("stress", stress))
                    },
                }
            )
        if index % 100 == 0:
            print(f"flow candidate {index}/{len(candidates)}; eligible={len(rows)}", flush=True)
    return rows


def _search_configs(
    state_curves: dict[str, DailyReturns],
    replays: dict[str, dict[str, DailyReturns]],
    periods: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    rows = []
    development_periods = (("train", periods["train"]), ("validation", periods["validation"]))
    for index, (candidate_id, curves) in enumerate(replays.items(), start=1):
        for state_weight in STATE_WEIGHTS:
            composites = {
                cost: {
                    split: _composite_returns(
                        state_curves[cost],
                        curves[cost],
                        state_weight,
                        period,
                    )
                    for split, period in development_periods
                }
                for cost in ("base", "stress")
            }
            for leverage in LEVERAGES:
                for loss_limit in LOSS_LIMITS:
                    for profit_target in PROFIT_TARGETS:
                        config = DefensiveConfig(
                            candidate_id,
                            state_weight,
                            leverage,
                            loss_limit,
                            profit_target,
                        )
                        results = {
                            cost: {
                                split: evaluate_monthly_risk_overlay(
                                    values,
                                    config.risk(Decimal("7") if cost == "base" else Decimal("15")),
                                )
                                for split, values in split_returns.items()
                            }
                            for cost, split_returns in composites.items()
                        }
                        if _development_eligible(results):
                            rows.append(
                                {
                                    "config": config,
                                    "results": results,
                                    "score": _development_score(results),
                                }
                            )
        print(f"flow config {index}/{len(replays)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _pair_shortlist(
    replays: dict[str, dict[str, DailyReturns]],
    periods: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    rows = []
    names = tuple(replays)
    development_periods = (("train", periods["train"]), ("validation", periods["validation"]))
    for left, right in combinations(names, 2):
        for left_weight in PAIR_WEIGHTS:
            costs = {
                cost: {
                    split: _composite_returns(
                        replays[left][cost],
                        replays[right][cost],
                        left_weight,
                        period,
                    )
                    for split, period in development_periods
                }
                for cost in ("base", "stress")
            }
            results = {
                cost: {split: _unlocked_result(values) for split, values in splits.items()}
                for cost, splits in costs.items()
            }
            if all(
                result.net_return > 0
                and result.max_drawdown >= Decimal("-0.35")
                and result.positive_month_rate >= Decimal("0.5")
                and not result.bankrupt
                for cost_results in results.values()
                for result in cost_results.values()
            ):
                pair_id = f"flow-pair-{left}-weight{left_weight}-{right}"
                full_returns = {
                    cost: tuple(
                        item
                        for _split, period in (
                            *development_periods,
                            ("confirmation", periods["confirmation"]),
                        )
                        for item in _composite_returns(
                            replays[left][cost],
                            replays[right][cost],
                            left_weight,
                            period,
                        )
                    )
                    for cost in ("base", "stress")
                }
                rows.append(
                    {
                        "id": pair_id,
                        "left": left,
                        "right": right,
                        "left_weight": left_weight,
                        "right_weight": Decimal("1") - left_weight,
                        "results": results,
                        "returns": full_returns,
                        "score": _development_score(results),
                    }
                )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    flow: dict[int, Any],
    candidates: tuple[FlowCandidate, ...],
    eligible_candidates: list[dict[str, Any]],
    eligible_configs: list[dict[str, Any]],
    single_audit: dict[str, Any],
    pair_rows: list[dict[str, Any]],
    selected_pairs: list[dict[str, Any]],
    pair_configs: list[dict[str, Any]],
    pair_audit: dict[str, Any],
    periods: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    candidates_by_id = {row["candidate"].id: row["candidate"] for row in eligible_candidates}
    pairs_by_id = {row["id"]: row for row in selected_pairs}
    aggregate_audit = _aggregate_confirmation_audit(single_audit, pair_audit)
    strict_count = aggregate_audit["strict_pass_count"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTC order-flow complement with monthly risk locks",
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
            "development_history_limit": "order-flow archives begin 2024-01-01",
        },
        "search": {
            "candidate_count": len(candidates),
            "independent_development_eligible_count": len(eligible_candidates),
            "configuration_grid_per_candidate": (
                len(STATE_WEIGHTS) * len(LEVERAGES) * len(LOSS_LIMITS) * len(PROFIT_TARGETS)
            ),
            "development_risk_eligible_count": len(eligible_configs),
            "state_weights": [float(value) for value in STATE_WEIGHTS],
            "leverages": [float(value) for value in LEVERAGES],
            "monthly_loss_limits": [float(value) for value in LOSS_LIMITS],
            "monthly_profit_targets": [float(value) for value in PROFIT_TARGETS],
            "eligible_candidates": [
                {
                    "candidate": row["candidate"].as_dict(),
                    "base_development": {
                        split: _research_summary(row["base"][split])
                        for split in ("train", "validation")
                    },
                    "stress_development": {
                        split: _research_summary(row["stress"][split])
                        for split in ("train", "validation")
                    },
                }
                for row in eligible_candidates
            ],
        },
        "selection": {
            "single_factor": _selection_payload(eligible_configs, single_audit, candidates_by_id),
            "two_factor": {
                "development_pair_count": len(pair_rows),
                "shortlist_size": len(selected_pairs),
                "development_risk_eligible_count": len(pair_configs),
                "top_development_pairs": [_pair_payload(row) for row in selected_pairs[:20]],
                **_pair_selection_payload(pair_configs, pair_audit, pairs_by_id),
            },
        },
        "confirmation_audit": {
            "configuration_count": aggregate_audit["configuration_count"],
            "strict_pass_count": strict_count,
            "single_factor": _audit_payload(single_audit, candidates_by_id),
            "two_factor": _pair_audit_payload(pair_audit, pairs_by_id),
            "best_complete_month_count": aggregate_audit["best_complete_month_count"],
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
                "At least one development-selected order-flow complement reached +15% in all "
                "seven complete reused-confirmation months under base and stress costs; limited "
                "development history and fresh forward evidence remain unresolved."
                if strict_count
                else "No development-selected order-flow complement reached +15% in all seven "
                "complete 2026 months under both base and stress costs."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "Order-flow archives provide only two complete development years.",
            "The order-flow complement was studied after observing prior 2026 failures.",
            "Reported buyer/seller direction is incomplete; tick-rule features are separate.",
            "Drawdown is daily-close only; liquidation and borrowing costs are not modeled.",
        ],
    }


def _aggregate_confirmation_audit(
    single_audit: dict[str, Any], pair_audit: dict[str, Any]
) -> dict[str, int]:
    return {
        "configuration_count": (
            single_audit["configuration_count"] + pair_audit["configuration_count"]
        ),
        "strict_pass_count": single_audit["strict_pass_count"] + pair_audit["strict_pass_count"],
        "best_complete_month_count": max(_best_count(single_audit), _best_count(pair_audit)),
    }


def _strict_complete_month_count(result: Any) -> int:
    return _strict_count(result)


def _research_summary(result: Any) -> dict[str, Any]:
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "monthly_returns": [
            {"label": label, "return": value} for label, value in result.monthly_returns
        ],
    }


def _with_candidate(
    payload: dict[str, Any] | None,
    candidates: dict[str, FlowCandidate],
) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "candidate": candidates[payload["config"]["candidate_id"]].as_dict(),
        **payload,
    }


def _selection_payload(
    configs: list[dict[str, Any]],
    audit: dict[str, Any],
    candidates: dict[str, FlowCandidate],
) -> dict[str, Any]:
    return {
        "development_risk_eligible_count": len(configs),
        "development_selected": _with_candidate(
            _audited_payload(audit["development_selected"]), candidates
        ),
        "top_development_configurations": [
            {
                "candidate": candidates[row["config"].candidate_id].as_dict(),
                "config": row["config"].as_dict(),
                "score": [float(value) for value in row["score"]],
            }
            for row in configs[:20]
        ],
    }


def _pair_selection_payload(
    configs: list[dict[str, Any]],
    audit: dict[str, Any],
    pairs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "development_selected": _with_pair(_audited_payload(audit["development_selected"]), pairs),
        "top_development_configurations": [
            {
                "pair": _pair_payload(pairs[row["config"].candidate_id]),
                "config": row["config"].as_dict(),
                "score": [float(value) for value in row["score"]],
            }
            for row in configs[:20]
        ],
    }


def _audit_payload(audit: dict[str, Any], candidates: dict[str, FlowCandidate]) -> dict[str, Any]:
    return {
        "configuration_count": audit["configuration_count"],
        "strict_pass_count": audit["strict_pass_count"],
        "best_complete_month_count": _best_count(audit),
        "best_confirmation_diagnostic": _with_candidate(
            _audited_payload(audit["best_confirmation"]), candidates
        ),
    }


def _pair_audit_payload(audit: dict[str, Any], pairs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "configuration_count": audit["configuration_count"],
        "strict_pass_count": audit["strict_pass_count"],
        "best_complete_month_count": _best_count(audit),
        "best_confirmation_diagnostic": _with_pair(
            _audited_payload(audit["best_confirmation"]), pairs
        ),
    }


def _best_count(audit: dict[str, Any]) -> int:
    return min(audit["best_confirmation"]["counts"].values()) if audit["best_confirmation"] else 0


def _with_pair(
    payload: dict[str, Any] | None,
    pairs: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {"pair": _pair_payload(pairs[payload["config"]["candidate_id"]]), **payload}


def _pair_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "left_candidate_id": row["left"],
        "left_weight": float(row["left_weight"]),
        "right_candidate_id": row["right"],
        "right_weight": float(row["right_weight"]),
        "score": [float(value) for value in row["score"]],
    }


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    lines = [
        f"# {payload['id']}",
        "",
        "BTC order-flow complement search for the strict monthly target.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        "## Search Result",
        "",
        f"- Order-flow candidates: `{payload['search']['candidate_count']}`.",
        f"- Independent development-eligible factors: "
        f"`{payload['search']['independent_development_eligible_count']}`.",
        f"- Development risk-eligible configurations: "
        f"`{payload['search']['development_risk_eligible_count']}`.",
        f"- Best reused-confirmation coverage: `{audit['best_complete_month_count']}/7`.",
        f"- Strict base-and-stress 7/7 configurations: `{audit['strict_pass_count']}`.",
        "",
        payload["decision"]["reason"],
        "Partial `2026-08` is excluded from strict counts.",
    ]
    for title, section in (
        (
            "Single-Factor Development Selection",
            payload["selection"]["single_factor"]["development_selected"],
        ),
        (
            "Best Single-Factor Confirmation Diagnostic",
            audit["single_factor"]["best_confirmation_diagnostic"],
        ),
        (
            "Two-Factor Development Selection",
            payload["selection"]["two_factor"]["development_selected"],
        ),
        (
            "Best Two-Factor Confirmation Diagnostic",
            audit["two_factor"]["best_confirmation_diagnostic"],
        ),
    ):
        if section:
            lines.extend(["", f"## {title}", "", _config_line(section), ""])
            lines.extend(_monthly_table(section))
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _readme(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    return "\n".join(
        [
            "# Order-Flow Complement",
            "",
            "This study combines development-eligible BTC 4h order-flow factors with the frozen",
            "market-state strategy and causal monthly risk locks. Development is limited to",
            "2024/2025; January-July 2026 is reused confirmation and August is excluded.",
            "",
            f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
            f"Best strict coverage: `{audit['best_complete_month_count']}/7`; base-and-stress "
            f"7/7 configurations: `{audit['strict_pass_count']}`.",
            "",
            "Reproduce from the repository root:",
            "",
            "```bash",
            ".venv/bin/python scripts/research/mine_order_flow_complement.py \\",
            "  --report-id order-flow-complement-20260815",
            "```",
            "",
        ]
    )


def _config_line(section: dict[str, Any]) -> str:
    config = section["config"]
    source = section.get("candidate", section.get("pair"))
    return (
        f"`{source['id']}`; state/flow "
        f"`{config['state_weight']:.0%}/{config['factor_weight']:.0%}`; leverage "
        f"`{config['leverage']:.2f}x`; monthly loss/profit locks "
        f"`{config['monthly_loss_limit']:.0%}/{config['monthly_profit_target']:.0%}`."
    )


def _monthly_table(section: dict[str, Any]) -> list[str]:
    base = section["confirmation"]["base"]["monthly_returns"]
    stress = {
        row["label"]: row["return"] for row in section["confirmation"]["stress"]["monthly_returns"]
    }
    return [
        "| Month | Base | Stress |",
        "|---|---:|---:|",
        *(f"| {row['label']} | {row['return']:.2%} | {stress[row['label']]:.2%} |" for row in base),
    ]


if __name__ == "__main__":
    main()
