#!/usr/bin/env python3
"""Search fast causal trend sleeves as complements to the frozen market-state strategy."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_defensive_factor_portfolio import (  # noqa: E402
    DefensiveConfig,
    _audited_payload,
    _composite_returns,
    _confirmation_audit,
    _development_eligible,
    _development_score,
    _public_result,
)
from mine_factor_portfolio import (  # noqa: E402
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    DISCOVERY,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    VALIDATION,
)
from mine_monthly_target_regime_router import (  # noqa: E402
    ASSETS,
    COMPLETE_CONFIRMATION_END,
    DEVELOPMENT_PERIOD,
    TARGET_MONTHLY_RETURN,
    _period_payload,
    _persistent_targets,
    _state_curves,
    _timestamp,
)

from mastermind_tick.bar_research import (  # noqa: E402
    ResearchBar,
    aggregate_bars,
    evaluate_targets,
    funding_by_bar,
    momentum_targets,
)
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import evaluate_monthly_risk_overlay  # noqa: E402
from mastermind_tick.factor_portfolio import (  # noqa: E402
    DailyReturns,
    PortfolioResult,
    decimal_returns,
    monthly_returns,
)

SHORTLIST_SIZE = 60
INTERVAL_CONFIGS = {
    240: {
        "lookbacks": (3, 6, 12, 18, 30, 42),
        "thresholds": (Decimal("0"), Decimal("0.005"), Decimal("0.01"), Decimal("0.02")),
    },
    1440: {
        "lookbacks": (1, 2, 3, 5, 7, 10, 15, 20),
        "thresholds": (Decimal("0"), Decimal("0.01"), Decimal("0.02"), Decimal("0.04")),
    },
}
CONFIRMATION_BARS = (1, 2)
PREFILTER_WEIGHTS = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75"))
STATE_WEIGHTS = tuple(
    Decimal(value) for value in ("0.25", "0.4", "0.5", "0.6", "0.75", "0.85", "0.9")
)
LEVERAGES = tuple(Decimal(value) for value in ("1", "1.25", "1.5", "2", "2.5", "3", "4", "5", "6"))
LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25"))
PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18"))


@dataclass(frozen=True)
class FastTrendCandidate:
    asset: str
    interval_minutes: int
    lookback_bars: int
    threshold: Decimal
    confirmation_bars: int
    bars: list[ResearchBar]
    funding: list[list[Any]]
    targets: tuple[int | None, ...]

    @property
    def id(self) -> str:
        threshold = f"{self.threshold:g}".replace(".", "p")
        return (
            f"{self.asset}-fast-momentum-{self.interval_minutes}m-lookback{self.lookback_bars}-"
            f"threshold{threshold}-confirm{self.confirmation_bars}-long_short"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset": self.asset,
            "interval_minutes": self.interval_minutes,
            "lookback_bars": self.lookback_bars,
            "lookback_days": self.lookback_bars * self.interval_minutes / 1440,
            "threshold": float(self.threshold),
            "confirmation_bars": self.confirmation_bars,
            "direction": "long_short",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/fast_trend_complement/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    print("loading frozen state strategy and BTC/ETH history", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state_curves = _state_curves(loaded, args.metrics_dir)
    candidates = _candidates(loaded)
    print(f"replaying {len(candidates)} fast closed-bar trend candidates", flush=True)
    replays = _replays(candidates)

    shortlist_rows = _prefilter(state_curves["base"], replays)
    shortlist = shortlist_rows[:SHORTLIST_SIZE]
    shortlist_replays = {row["candidate_id"]: replays[row["candidate_id"]] for row in shortlist}
    print(f"searching risk controls for {len(shortlist)} development candidates", flush=True)
    eligible = _search_development(state_curves, shortlist_replays)
    print(f"auditing {len(eligible):,} development-eligible configurations", flush=True)
    audit = _confirmation_audit(state_curves, shortlist_replays, eligible)

    payload = _report(loaded, candidates, shortlist_rows, shortlist, eligible, audit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"fast-trend-complement-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidates(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
) -> list[FastTrendCandidate]:
    result = []
    for asset, (source_bars, funding_rates) in loaded.items():
        for interval, values in INTERVAL_CONFIGS.items():
            bars = aggregate_bars(source_bars, interval)
            funding = funding_by_bar(bars, funding_rates)
            for lookback in values["lookbacks"]:
                for threshold in values["thresholds"]:
                    raw = momentum_targets(
                        bars,
                        int(lookback),
                        float(threshold),
                        "long_short",
                    )
                    for confirmation in CONFIRMATION_BARS:
                        result.append(
                            FastTrendCandidate(
                                asset,
                                interval,
                                int(lookback),
                                Decimal(threshold),
                                confirmation,
                                bars,
                                funding,
                                _persistent_targets(raw, confirmation),
                            )
                        )
    return result


def _replays(candidates: list[FastTrendCandidate]) -> dict[str, dict[str, DailyReturns]]:
    result = {}
    for index, candidate in enumerate(candidates, start=1):
        costs = {}
        for cost_name, fee, slippage in (
            ("base", BASE_FEE_BPS, BASE_SLIPPAGE_BPS),
            ("stress", STRESS_FEE_BPS, STRESS_SLIPPAGE_BPS),
        ):
            rows = []
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
                rows.extend(decimal_returns(replay.daily_returns))
            costs[cost_name] = tuple(rows)
        result[candidate.id] = costs
        if index % 40 == 0:
            print(f"trend replay {index}/{len(candidates)}", flush=True)
    return result


def _prefilter(
    state_returns: DailyReturns,
    replays: dict[str, dict[str, DailyReturns]],
) -> list[dict[str, Any]]:
    rows = []
    for candidate_id, curves in replays.items():
        best = None
        for state_weight in PREFILTER_WEIGHTS:
            results = {
                split: _unlocked_result(
                    _composite_returns(
                        state_returns,
                        curves["base"],
                        state_weight,
                        period,
                    )
                )
                for split, period in (("discovery", DISCOVERY), ("validation", VALIDATION))
            }
            if all(result.net_return > 0 and not result.bankrupt for result in results.values()):
                score = (
                    min(_target_rate(result) for result in results.values()),
                    sum((_target_rate(result) for result in results.values()), Decimal("0")),
                    min(result.positive_month_rate for result in results.values()),
                    min(result.worst_month for result in results.values()),
                    min(result.net_return for result in results.values()),
                    min(result.max_drawdown for result in results.values()),
                )
                if best is None or score > best["score"]:
                    best = {
                        "candidate_id": candidate_id,
                        "state_weight": state_weight,
                        "results": results,
                        "score": score,
                    }
        if best:
            rows.append(best)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _unlocked_result(rows: DailyReturns) -> PortfolioResult:
    equity = Decimal("100000")
    peak = equity
    drawdown = Decimal("0")
    for _label, value in rows:
        equity *= Decimal("1") + value
        peak = max(peak, equity)
        if peak > 0:
            drawdown = min(drawdown, equity / peak - Decimal("1"))
    return PortfolioResult(
        initial_equity=Decimal("100000"),
        final_equity=equity,
        net_return=equity / Decimal("100000") - Decimal("1"),
        max_drawdown=drawdown,
        bankrupt=equity <= 0,
        daily_returns=rows,
        monthly_returns=monthly_returns(rows),
    )


def _search_development(
    state_curves: dict[str, DailyReturns],
    replays: dict[str, dict[str, DailyReturns]],
) -> list[dict[str, Any]]:
    rows = []
    for index, (candidate_id, curves) in enumerate(replays.items(), start=1):
        for state_weight in STATE_WEIGHTS:
            composites = {
                cost_name: {
                    split: _composite_returns(
                        state_curves[cost_name],
                        curves[cost_name],
                        state_weight,
                        period,
                    )
                    for split, period in (
                        ("discovery", DISCOVERY),
                        ("validation", VALIDATION),
                    )
                }
                for cost_name in ("base", "stress")
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
                            cost_name: {
                                split: evaluate_monthly_risk_overlay(
                                    returns,
                                    config.risk(
                                        Decimal("7") if cost_name == "base" else Decimal("15")
                                    ),
                                )
                                for split, returns in split_curves.items()
                            }
                            for cost_name, split_curves in composites.items()
                        }
                        if _development_eligible(results):
                            rows.append(
                                {
                                    "config": config,
                                    "results": results,
                                    "score": _development_score(results),
                                }
                            )
        if index % 10 == 0:
            print(f"trend config {index}/{len(replays)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _target_rate(result: PortfolioResult) -> Decimal:
    if not result.monthly_returns:
        return Decimal("0")
    return Decimal(
        sum(value >= TARGET_MONTHLY_RETURN for _label, value in result.monthly_returns)
    ) / Decimal(len(result.monthly_returns))


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    candidates: list[FastTrendCandidate],
    prefilter: list[dict[str, Any]],
    shortlist: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    audit: dict[str, Any],
) -> dict[str, Any]:
    candidate_by_id = {candidate.id: candidate for candidate in candidates}
    strict_count = audit["strict_pass_count"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "fast causal time-series momentum complement with monthly risk locks",
        "data": {
            asset: {
                "first_bar": _timestamp(values[0][0].start_ms),
                "last_bar": _timestamp(values[0][-1].end_ms),
            }
            for asset, values in loaded.items()
        },
        "protocol": {
            "discovery": _period_payload(DISCOVERY),
            "validation": _period_payload(VALIDATION),
            "confirmation": _period_payload(CONFIRMATION),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "signal_timing": "closed 4h or daily bar; execute at next bar open",
        },
        "search": {
            "candidate_count": len(candidates),
            "development_positive_prefilter_count": len(prefilter),
            "shortlist_size": len(shortlist),
            "configuration_grid_per_candidate": (
                len(STATE_WEIGHTS) * len(LEVERAGES) * len(LOSS_LIMITS) * len(PROFIT_TARGETS)
            ),
            "development_risk_eligible_count": len(eligible),
            "top_prefilter_candidates": [
                {
                    "candidate": candidate_by_id[row["candidate_id"]].as_dict(),
                    "state_weight": float(row["state_weight"]),
                    "score": [float(value) for value in row["score"]],
                    "development": {
                        split: _public_result(result) for split, result in row["results"].items()
                    },
                }
                for row in shortlist[:20]
            ],
        },
        "selection": {
            "development_selected": _with_candidate(
                _audited_payload(audit["development_selected"]), candidate_by_id
            ),
            "top_development_configurations": [
                {
                    "candidate": candidate_by_id[row["config"].candidate_id].as_dict(),
                    "config": row["config"].as_dict(),
                    "score": [float(value) for value in row["score"]],
                }
                for row in eligible[:20]
            ],
        },
        "confirmation_audit": {
            "configuration_count": audit["configuration_count"],
            "strict_pass_count": strict_count,
            "best_complete_month_count": (
                min(audit["best_confirmation"]["counts"].values())
                if audit["best_confirmation"]
                else 0
            ),
            "best_confirmation_diagnostic": _with_candidate(
                _audited_payload(audit["best_confirmation"]), candidate_by_id
            ),
            "strict_examples": [
                _with_candidate(_audited_payload(item), candidate_by_id)
                for item in audit["strict_examples"]
            ],
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
                "At least one development-selected fast-trend complement reached +15% in all "
                "seven complete reused-confirmation months under base and stress costs; fresh "
                "forward evidence remains required."
                if strict_count
                else "No development-selected fast-trend complement reached +15% in all seven "
                "complete 2026 months under both base and stress costs."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The fast trend grid was introduced after observing prior 2026 failures.",
            "The frozen market-state baseline was selected in prior overlapping research.",
            "Monthly locks react after daily closes and incur explicit exposure turnover costs.",
            "Drawdown is daily-close only; liquidation and borrowing costs are not modeled.",
        ],
    }


def _with_candidate(
    payload: dict[str, Any] | None,
    candidates: dict[str, FastTrendCandidate],
) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "candidate": candidates[payload["config"]["candidate_id"]].as_dict(),
        **payload,
    }


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    lines = [
        f"# {payload['id']}",
        "",
        "Fast closed-bar trend complement search for the strict monthly target.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        "## Search Result",
        "",
        f"- Trend candidates: `{payload['search']['candidate_count']}`.",
        f"- Development shortlist: `{payload['search']['shortlist_size']}`.",
        f"- Development risk-eligible configurations: "
        f"`{payload['search']['development_risk_eligible_count']}`.",
        f"- Best reused-confirmation coverage: `{audit['best_complete_month_count']}/7`.",
        f"- Strict base-and-stress 7/7 configurations: `{audit['strict_pass_count']}`.",
        "",
        payload["decision"]["reason"],
        "Partial `2026-08` is excluded from strict counts.",
    ]
    for title, section in (
        ("Development-selected Configuration", payload["selection"]["development_selected"]),
        ("Best Reused-Confirmation Diagnostic", audit["best_confirmation_diagnostic"]),
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
            "# Fast Trend Complement",
            "",
            "This study tests predeclared 4h/daily BTC/ETH time-series momentum sleeves around",
            "the frozen market-state strategy. Selection uses 2021-2025 only; January-July 2026",
            "is reused confirmation and partial August is excluded.",
            "",
            f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
            f"Best strict coverage: `{audit['best_complete_month_count']}/7`; base-and-stress "
            f"7/7 configurations: `{audit['strict_pass_count']}`.",
            "",
            "Reproduce from the repository root:",
            "",
            "```bash",
            ".venv/bin/python scripts/research/mine_fast_trend_complement.py \\",
            "  --report-id fast-trend-complement-20260815",
            "```",
            "",
        ]
    )


def _config_line(section: dict[str, Any]) -> str:
    candidate = section["candidate"]
    config = section["config"]
    return (
        f"`{candidate['id']}`; state/factor "
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
