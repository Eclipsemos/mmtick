#!/usr/bin/env python3
"""Validate BTC exposure targets with separately collateralized spot and futures wallets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import benchmark
from research_btc_frozen_ensemble import build_targets
from research_btc_funding_aware_exposure import (
    BULL_EXPOSURES,
    FUNDING_THRESHOLDS,
    PERIODS,
    funding_aware_targets,
)
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import ResearchBar, funding_by_bar
from mastermind_tick.models import FundingRate
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT_DIR = Path("reports/experiments/btc_collateral_architecture/2026-09-02")
SPOT_CAPS = tuple(Decimal(value) for value in ("0", "0.25", "0.5", "0.75"))
OPERATIONAL_SPOT_CAP = Decimal("0.75")
BASE_MAINTENANCE_RATE = Decimal("0.004")
STRESS_MAINTENANCE_RATE = Decimal("0.02")
MAX_FUTURES_LEVERAGE = Decimal("3")
MAX_OPERATIONAL_TOTAL_EXPOSURE = OPERATIONAL_SPOT_CAP + MAX_FUTURES_LEVERAGE * (
    Decimal("1") - OPERATIONAL_SPOT_CAP
)


@dataclass(frozen=True)
class SegregatedResult:
    net_return: float
    max_drawdown: float
    total_fees: float
    total_funding: float
    rebalances: int
    liquidated: bool
    minimum_margin_ratio: float | None
    maximum_futures_leverage: float
    maximum_observed_futures_leverage: float
    maximum_controlled_open_futures_leverage: float
    equity_curve: tuple[tuple[int, float], ...] = ()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    primary_targets = build_targets(bars, funding)["primary"]
    sensitivity = evaluate_caps(bars, funding, primary_targets, splits)
    walk_forward = operational_walk_forward(bars, funding)
    evaluation_years = years_between(splits["full"][0], bars[-1].end_ms)
    operational_full = sensitivity[str(OPERATIONAL_SPOT_CAP)]["full"]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "wallets": (
                "spot position and USD-M collateral are separate; only futures-wallet equity "
                "backs futures maintenance margin"
            ),
            "spot_caps": [str(value) for value in SPOT_CAPS],
            "operational_spot_cap": str(OPERATIONAL_SPOT_CAP),
            "bull_futures_leverage": "orders capped at 3x at the 1.5x total target",
            "leverage_scope": (
                "3x limits leverage when an order is sized; effective leverage may rise after "
                "futures-collateral losses before the next rebalance"
            ),
            "maximum_total_exposure": "1.5x for the conservative selected family",
            "maintenance": {
                "base": str(BASE_MAINTENANCE_RATE),
                "stress": str(STRESS_MAINTENANCE_RATE),
            },
            "funding": "charged on the entire futures sleeve",
            "execution": "fixed quantities between sparse target changes",
        },
        "data": {
            "warmup_start": iso(bars[0].start_ms),
            "evaluation_start": iso(splits["full"][0]),
            "last": iso(bars[-1].end_ms),
            "bars": len(bars),
        },
        "fixed_primary_sensitivity": sensitivity,
        "walk_forward": walk_forward,
        "summary": {
            "full_stress_cagr": annualized_return(
                operational_full["stress"]["net_return"], evaluation_years
            ),
            "full_benchmark_cagr": annualized_return(
                operational_full["buy_and_hold"]["net_return"], evaluation_years
            ),
            "walk_forward_stress_cagr": annualized_return(
                walk_forward["stress_compound_return"], walk_forward["years_elapsed"]
            ),
            "walk_forward_benchmark_cagr": annualized_return(
                walk_forward["benchmark_compound_return"], walk_forward["years_elapsed"]
            ),
            "continuous_effective_leverage_cap_passed": (
                operational_full["stress"]["maximum_observed_futures_leverage"]
                <= float(MAX_FUTURES_LEVERAGE)
            ),
        },
        "status": (
            "FORWARD_OBSERVATION"
            if walk_forward["stress_compound_return"] > walk_forward["benchmark_compound_return"]
            and not walk_forward["any_liquidation"]
            else "RESEARCH_ONLY"
        ),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def replay_segregated(
    bars: list[ResearchBar],
    targets,
    funding: list[list[FundingRate]],
    start_ms: int,
    end_ms: int,
    *,
    spot_cap: Decimal,
    maintenance_rate: Decimal,
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
    record_equity: bool = False,
    enforce_effective_leverage_cap: bool = False,
    maximum_futures_leverage: Decimal = MAX_FUTURES_LEVERAGE,
) -> SegregatedResult:
    if not Decimal("0") <= spot_cap < Decimal("1"):
        raise ValueError("segregated spot cap must be between zero inclusive and one exclusive")
    if maintenance_rate < 0:
        raise ValueError("maintenance rate cannot be negative")
    if maximum_futures_leverage <= 0:
        raise ValueError("maximum futures leverage must be positive")
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        raise ValueError("no bars in requested range")
    pending = Decimal("1")
    previous_index = selected[0] - 1
    while previous_index >= 0:
        if targets[previous_index] is not None:
            pending = Decimal(targets[previous_index])
            break
        previous_index -= 1
    fee_rate = fee_bps / Decimal("10000")
    cost_rate = fee_rate + slippage_bps / Decimal("10000")
    initial_equity = Decimal("100000")
    spot_quantity = Decimal("0")
    futures_quantity = Decimal("0")
    futures_equity = initial_equity
    active_target = Decimal("0")
    previous_close = None
    peak = initial_equity
    max_drawdown = Decimal("0")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    rebalances = 0
    liquidated = False
    minimum_margin_ratio = None
    maximum_sized_futures_leverage = Decimal("0")
    maximum_observed_futures_leverage = Decimal("0")
    maximum_controlled_open_futures_leverage = Decimal("0")
    equity_curve: list[tuple[int, float]] = []

    for index in selected:
        bar = bars[index]
        if previous_close is not None:
            futures_equity += futures_quantity * (bar.open - previous_close)
        if futures_quantity:
            maintenance_open = maintenance_rate * abs(futures_quantity) * bar.open
            if futures_equity <= maintenance_open:
                liquidated = True
                max_drawdown = min(max_drawdown, Decimal("-1"))
                if record_equity:
                    equity_curve.append((bar.end_ms, 0.0))
                break
            observed_leverage = abs(futures_quantity) * bar.open / futures_equity
            maximum_observed_futures_leverage = max(
                maximum_observed_futures_leverage, observed_leverage
            )
            if enforce_effective_leverage_cap and observed_leverage > maximum_futures_leverage:
                capped_quantity = (
                    maximum_futures_leverage * futures_equity / bar.open * Decimal("0.99")
                )
                if futures_quantity < 0:
                    capped_quantity = -capped_quantity
                reduced_notional = abs(futures_quantity - capped_quantity) * bar.open
                futures_equity -= reduced_notional * cost_rate
                total_fees += reduced_notional * fee_rate
                futures_quantity = capped_quantity
                rebalances += 1
                if futures_equity <= 0:
                    liquidated = True
                    max_drawdown = min(max_drawdown, Decimal("-1"))
                    if record_equity:
                        equity_curve.append((bar.end_ms, 0.0))
                    break
            if futures_equity > 0:
                controlled_leverage = abs(futures_quantity) * bar.open / futures_equity
                maximum_controlled_open_futures_leverage = max(
                    maximum_controlled_open_futures_leverage, controlled_leverage
                )
        total_at_open = spot_quantity * bar.open + futures_equity
        if total_at_open <= 0:
            liquidated = True
            max_drawdown = min(max_drawdown, Decimal("-1"))
            if record_equity:
                equity_curve.append((bar.end_ms, 0.0))
            break
        if pending != active_target:
            spot_quantity, futures_quantity, futures_equity, cost, fee = rebalance_wallets(
                total_at_open,
                bar.open,
                pending,
                spot_cap,
                spot_quantity,
                futures_quantity,
                cost_rate,
                fee_rate,
                maximum_futures_leverage,
            )
            total_fees += fee
            total_at_open -= cost
            active_target = pending
            rebalances += 1
            if futures_quantity:
                leverage = abs(futures_quantity) * bar.open / futures_equity
                maximum_sized_futures_leverage = max(maximum_sized_futures_leverage, leverage)
                maximum_observed_futures_leverage = max(maximum_observed_futures_leverage, leverage)
                if enforce_effective_leverage_cap and leverage > maximum_futures_leverage:
                    capped_quantity = (
                        maximum_futures_leverage * futures_equity / bar.open * Decimal("0.99")
                    )
                    if futures_quantity < 0:
                        capped_quantity = -capped_quantity
                    reduced_notional = abs(futures_quantity - capped_quantity) * bar.open
                    futures_equity -= reduced_notional * cost_rate
                    total_fees += reduced_notional * fee_rate
                    futures_quantity = capped_quantity
                    rebalances += 1
                if futures_equity > 0:
                    controlled_leverage = abs(futures_quantity) * bar.open / futures_equity
                    maximum_controlled_open_futures_leverage = max(
                        maximum_controlled_open_futures_leverage, controlled_leverage
                    )
        for event in funding[index]:
            amount = -(futures_quantity * event.mark_price * event.rate)
            futures_equity += amount
            total_funding += amount
        if futures_equity <= maintenance_rate * abs(futures_quantity) * bar.open:
            liquidated = True
            max_drawdown = min(max_drawdown, Decimal("-1"))
            if record_equity:
                equity_curve.append((bar.end_ms, 0.0))
            break
        futures_low_equity = futures_equity + futures_quantity * (bar.low - bar.open)
        maintenance_low = maintenance_rate * abs(futures_quantity) * bar.low
        if futures_low_equity <= maintenance_low:
            liquidated = True
            max_drawdown = min(max_drawdown, Decimal("-1"))
            if record_equity:
                equity_curve.append((bar.end_ms, 0.0))
            break
        if futures_quantity:
            ratio = futures_low_equity / (abs(futures_quantity) * bar.low)
            minimum_margin_ratio = (
                ratio if minimum_margin_ratio is None else min(minimum_margin_ratio, ratio)
            )
            low_leverage = abs(futures_quantity) * bar.low / futures_low_equity
            maximum_observed_futures_leverage = max(maximum_observed_futures_leverage, low_leverage)
        futures_equity += futures_quantity * (bar.close - bar.open)
        maintenance_close = maintenance_rate * abs(futures_quantity) * bar.close
        if futures_equity <= maintenance_close:
            liquidated = True
            max_drawdown = min(max_drawdown, Decimal("-1"))
            if record_equity:
                equity_curve.append((bar.end_ms, 0.0))
            break
        total_equity = spot_quantity * bar.close + futures_equity
        total_low = spot_quantity * bar.low + futures_low_equity
        peak = max(peak, total_equity)
        max_drawdown = min(
            max_drawdown,
            total_equity / peak - Decimal("1"),
            total_low / peak - Decimal("1"),
        )
        if record_equity:
            equity_curve.append((bar.end_ms, float(total_equity)))
        if targets[index] is not None:
            pending = Decimal(targets[index])
        previous_close = bar.close

    if liquidated:
        final_equity = Decimal("0")
    else:
        final_price = bars[selected[-1]].close
        final_equity = spot_quantity * final_price + futures_equity
        closing_notional = (abs(spot_quantity) + abs(futures_quantity)) * final_price
        final_equity -= closing_notional * cost_rate
        total_fees += closing_notional * fee_rate
        if record_equity and equity_curve:
            equity_curve[-1] = (equity_curve[-1][0], float(final_equity))
    return SegregatedResult(
        net_return=float(final_equity / initial_equity - Decimal("1")),
        max_drawdown=float(max_drawdown),
        total_fees=float(total_fees),
        total_funding=float(total_funding),
        rebalances=rebalances,
        liquidated=liquidated,
        minimum_margin_ratio=(
            float(minimum_margin_ratio) if minimum_margin_ratio is not None else None
        ),
        maximum_futures_leverage=float(maximum_sized_futures_leverage),
        maximum_observed_futures_leverage=float(maximum_observed_futures_leverage),
        maximum_controlled_open_futures_leverage=float(maximum_controlled_open_futures_leverage),
        equity_curve=tuple(equity_curve),
    )


def rebalance_wallets(
    equity_before_cost,
    price,
    target,
    spot_cap,
    current_spot,
    current_futures,
    cost_rate,
    fee_rate,
    maximum_futures_leverage=MAX_FUTURES_LEVERAGE,
):
    if not -Decimal("3") <= target <= Decimal("3"):
        raise ValueError("target exposure must be between negative three and three")
    maximum_total_target = (
        maximum_futures_leverage * (Decimal("1") - spot_cap)
        if target < 0
        else spot_cap + maximum_futures_leverage * (Decimal("1") - spot_cap)
    )
    if abs(target) > maximum_total_target:
        raise ValueError("target exceeds maximum futures leverage")
    spot_exposure = min(max(target, Decimal("0")), spot_cap)
    futures_exposure = target - spot_exposure
    current_spot_notional = current_spot * price
    current_futures_notional = current_futures * price

    def cost_equation(equity):
        changed = abs(spot_exposure * equity - current_spot_notional) + abs(
            futures_exposure * equity - current_futures_notional
        )
        return equity + changed * cost_rate - equity_before_cost

    if cost_rate == 0:
        sizing_equity = equity_before_cost
    else:
        lower = Decimal("0")
        upper = equity_before_cost
        if cost_equation(lower) > 0:
            raise ValueError("rebalance costs exceed available equity")
        for _ in range(128):
            midpoint = (lower + upper) / Decimal("2")
            if midpoint == lower or midpoint == upper:
                break
            if cost_equation(midpoint) <= 0:
                lower = midpoint
            else:
                upper = midpoint
        sizing_equity = lower
    desired_spot = spot_exposure * sizing_equity / price
    desired_futures = futures_exposure * sizing_equity / price
    changed_notional = (
        abs(desired_spot - current_spot) + abs(desired_futures - current_futures)
    ) * price
    equity_after_cost = equity_before_cost - changed_notional * cost_rate
    spot_value = desired_spot * price
    maximum_futures_notional = maximum_futures_leverage * (equity_after_cost - spot_value)
    if abs(desired_futures) * price > maximum_futures_notional:
        desired_futures = (maximum_futures_notional / price).next_minus()
        if futures_exposure < 0:
            desired_futures = -desired_futures
        changed_notional = (
            abs(desired_spot - current_spot) + abs(desired_futures - current_futures)
        ) * price
        equity_after_cost = equity_before_cost - changed_notional * cost_rate
    futures_equity = equity_after_cost - spot_value
    if desired_futures and futures_equity <= 0:
        raise ValueError("target leaves no futures collateral")
    if abs(desired_futures) * price > maximum_futures_leverage * futures_equity:
        raise ValueError("target exceeds maximum futures leverage")
    return (
        desired_spot,
        desired_futures,
        futures_equity,
        changed_notional * cost_rate,
        changed_notional * fee_rate,
    )


def evaluate_caps(bars, funding, targets, splits):
    output = {}
    for cap in SPOT_CAPS:
        output[str(cap)] = {}
        for split, (start, end) in splits.items():
            baseline = benchmark(bars, start, end)
            base = replay_segregated(
                bars,
                targets,
                funding,
                start,
                end,
                spot_cap=cap,
                maintenance_rate=BASE_MAINTENANCE_RATE,
            )
            stress = replay_segregated(
                bars,
                targets,
                funding,
                start,
                end,
                spot_cap=cap,
                maintenance_rate=STRESS_MAINTENANCE_RATE,
                fee_bps=Decimal("10"),
                slippage_bps=Decimal("5"),
            )
            output[str(cap)][split] = {
                "buy_and_hold": baseline,
                "base": asdict(base),
                "stress": asdict(stress),
            }
    return output


def operational_walk_forward(bars, funding):
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    candidates = []
    for periods in PERIODS:
        for bull_exposure in BULL_EXPOSURES:
            if bull_exposure > MAX_OPERATIONAL_TOTAL_EXPOSURE:
                continue
            regime = map_targets_to_source(
                len(bars),
                three_state_targets(aggregate, periods, Decimal("0"), bull_exposure),
                ends,
            )
            for threshold in FUNDING_THRESHOLDS:
                candidates.append(
                    {
                        "id": (
                            f"4h-{'-'.join(map(str, periods))}-bull{bull_exposure}x-"
                            f"funding-le-{threshold}"
                        ),
                        "bull_exposure": bull_exposure,
                        "targets": funding_aware_targets(regime, funding, bull_exposure, threshold),
                    }
                )
    last_year = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year
    base_equity = Decimal("1")
    stress_equity = Decimal("1")
    benchmark_equity = Decimal("1")
    rows = []
    for year in range(2022, last_year + 1):
        train_start = utc_ms(2020, 1, 1)
        train_end = utc_ms(year, 1, 1) - 1
        test_start = utc_ms(year, 1, 1)
        test_end = min(utc_ms(year + 1, 1, 1) - 1, bars[-1].end_ms)
        selected, training = select_operational_candidate(
            bars, funding, candidates, train_start, train_end
        )
        base = replay_segregated(
            bars,
            selected["targets"],
            funding,
            test_start,
            test_end,
            spot_cap=OPERATIONAL_SPOT_CAP,
            maintenance_rate=BASE_MAINTENANCE_RATE,
        )
        stress = replay_segregated(
            bars,
            selected["targets"],
            funding,
            test_start,
            test_end,
            spot_cap=OPERATIONAL_SPOT_CAP,
            maintenance_rate=STRESS_MAINTENANCE_RATE,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
        )
        baseline = benchmark(bars, test_start, test_end)
        base_equity *= Decimal(str(1 + base.net_return))
        stress_equity *= Decimal(str(1 + stress.net_return))
        benchmark_equity *= Decimal(str(1 + baseline["net_return"]))
        rows.append(
            {
                "year": year,
                "selected_id": selected["id"],
                "training": training,
                "base": asdict(base),
                "stress": asdict(stress),
                "buy_and_hold": baseline,
            }
        )
    return {
        "spot_cap": str(OPERATIONAL_SPOT_CAP),
        "years": rows,
        "years_elapsed": years_between(utc_ms(2022, 1, 1), bars[-1].end_ms),
        "base_compound_return": float(base_equity - Decimal("1")),
        "stress_compound_return": float(stress_equity - Decimal("1")),
        "benchmark_compound_return": float(benchmark_equity - Decimal("1")),
        "any_liquidation": any(
            row["base"]["liquidated"] or row["stress"]["liquidated"] for row in rows
        ),
    }


def select_operational_candidate(bars, funding, candidates, start, end):
    baseline = benchmark(bars, start, end)
    evaluated = []
    for candidate in candidates:
        base = replay_segregated(
            bars,
            candidate["targets"],
            funding,
            start,
            end,
            spot_cap=OPERATIONAL_SPOT_CAP,
            maintenance_rate=BASE_MAINTENANCE_RATE,
        )
        stress = replay_segregated(
            bars,
            candidate["targets"],
            funding,
            start,
            end,
            spot_cap=OPERATIONAL_SPOT_CAP,
            maintenance_rate=STRESS_MAINTENANCE_RATE,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
        )
        evaluated.append((candidate, base, stress))
    eligible = [
        item
        for item in evaluated
        if not item[1].liquidated
        and not item[2].liquidated
        and item[2].net_return > baseline["net_return"]
        and item[1].max_drawdown >= baseline["max_drawdown"]
        and item[2].maximum_futures_leverage <= float(MAX_FUTURES_LEVERAGE)
    ]
    pool = eligible or [item for item in evaluated if not item[1].liquidated]
    if eligible:
        candidate, base, stress = min(
            pool, key=lambda item: (item[0]["bull_exposure"], -item[2].net_return)
        )
    else:
        candidate, base, stress = max(pool, key=lambda item: item[2].net_return)
    return candidate, {
        "eligible_candidates": len(eligible),
        "base_return": base.net_return,
        "stress_return": stress.net_return,
        "benchmark_return": baseline["net_return"],
        "max_drawdown": base.max_drawdown,
        "maximum_futures_leverage": base.maximum_futures_leverage,
    }


def markdown(payload):
    lines = [
        "# BTC 可分离抵押账户架构",
        "",
        "现货和USD-M抵押钱包分开建模；现货不能免费支撑永续保证金。Funding按全部永续名义金额计收。",
        "3X限制的是下单时的合约名义金额/合约钱包权益；亏损后有效杠杆仍可能被动升高。",
        "",
        "## 冻结主信号的资金结构敏感性",
        "",
        "| 现货上限 | 全样本压力 | B&H | OOS压力 | 最大DD | 下单杠杆 | 观察有效杠杆 | 清算 |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for cap, result in payload["fixed_primary_sensitivity"].items():
        full = result["full"]
        oos = result["oos"]
        lines.append(
            f"| {cap}X | {pct(full['stress']['net_return'])} | "
            f"{pct(full['buy_and_hold']['net_return'])} | "
            f"{pct(oos['stress']['net_return'])} | {pct(full['stress']['max_drawdown'])} | "
            f"{full['stress']['maximum_futures_leverage']:.2f}X | "
            f"{full['stress']['maximum_observed_futures_leverage']:.2f}X | "
            f"{'是' if full['stress']['liquidated'] else '否'} |"
        )
    walk = payload["walk_forward"]
    lines += [
        "",
        "## 75%现货架构年度Walk-Forward",
        "",
        "| 年份 | 训练期选择 | 压力收益 | B&H | 下单杠杆 | 观察有效杠杆 | 清算 |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in walk["years"]:
        lines.append(
            f"| {row['year']} | `{row['selected_id']}` | "
            f"{pct(row['stress']['net_return'])} | "
            f"{pct(row['buy_and_hold']['net_return'])} | "
            f"{row['stress']['maximum_futures_leverage']:.2f}X | "
            f"{row['stress']['maximum_observed_futures_leverage']:.2f}X | "
            f"{'是' if row['stress']['liquidated'] else '否'} |"
        )
    lines += [
        "",
        f"复合基础：{pct(walk['base_compound_return'])}；"
        f"压力：{pct(walk['stress_compound_return'])}；"
        f"B&H：{pct(walk['benchmark_compound_return'])}。",
        "",
        f"Walk-Forward压力年化：{pct(payload['summary']['walk_forward_stress_cagr'])}；"
        f"B&H年化：{pct(payload['summary']['walk_forward_benchmark_cagr'])}。",
        "",
        "历史中没有下达超过3X的订单，但有效杠杆最高超过3X，因此不满足“任意时刻有效杠杆"
        "均不超过3X”的更严格定义。若采用该定义，需要另测主动减仓或止损。",
        "",
        f"状态：**{payload['status']}**。",
        "",
    ]
    return "\n".join(lines)


def utc_ms(year, month, day):
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def years_between(start_ms, end_ms):
    return (end_ms - start_ms) / (365.2425 * 24 * 60 * 60 * 1000)


def annualized_return(net_return, years):
    return (1 + net_return) ** (1 / years) - 1


if __name__ == "__main__":
    main()
