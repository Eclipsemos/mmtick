#!/usr/bin/env python3
"""Tick-level anchored walk-forward study for session-recovery re-entry."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from mastermind_tick.backtest import ReplayParameters, ReplayResult, run_parameter_grid
from mastermind_tick.config import InstrumentSettings, load_settings

THRESHOLDS = (0.25, 0.5, 0.75)
WINDOW_BARS = (2, 4)


def epoch(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)


def parameter_grid() -> list[ReplayParameters]:
    parameters = [ReplayParameters(32, 3.0, variant="baseline")]
    parameters.extend(
        ReplayParameters(
            32,
            3.0,
            variant=f"session_{scope}_{threshold:g}atr_{bars}bars",
            session_reentry_threshold_atr=threshold,
            session_reentry_window_bars=bars,
            session_reentry_scope=scope,
        )
        for scope in ("0816",)
        for threshold in THRESHOLDS
        for bars in WINDOW_BARS
    )
    parameters.extend(
        ReplayParameters(
            32,
            3.0,
            variant=f"session_2130_0.5atr_{bars}bars",
            session_reentry_threshold_atr=0.5,
            session_reentry_window_bars=bars,
            session_reentry_scope="2130",
        )
        for bars in WINDOW_BARS
    )
    parameters.append(
        ReplayParameters(
            32,
            3.0,
            variant="session_0816_2130_0.5atr_2bars",
            session_reentry_threshold_atr=0.5,
            session_reentry_window_bars=2,
            session_reentry_scope="0816_2130",
        )
    )
    return parameters


def run_segment(
    database: Path,
    instrument: InstrumentSettings,
    parameters: list[ReplayParameters],
    start_ms: int,
    end_ms: int | None,
) -> dict[str, ReplayResult]:
    settings = replace(load_settings(), database_path=database)
    _, results = run_parameter_grid(
        settings,
        instrument,
        parameters,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    return {
        parameter.variant: result for parameter, result in zip(parameters, results, strict=True)
    }


def choose_candidate(results: dict[str, ReplayResult]) -> ReplayResult:
    baseline = results["baseline"]
    eligible = [
        result
        for name, result in results.items()
        if name != "baseline" and result.max_drawdown >= baseline.max_drawdown - 0.02
    ]
    return max(eligible, key=lambda item: (item.net_return, item.max_drawdown))


def percentage(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def result_cells(result: ReplayResult) -> list[str]:
    return [
        percentage(result.net_return),
        percentage(result.max_drawdown),
        str(result.completed_trades),
        percentage(result.win_rate),
        str(result.session_reentry_signals),
        f"{result.total_fees:,.2f}",
    ]


def build_report(
    train: dict[str, ReplayResult],
    validation: dict[str, ReplayResult],
    outer: dict[str, ReplayResult],
    selected: ReplayResult,
    stress_validation: dict[str, ReplayResult],
    stress_outer: dict[str, ReplayResult],
) -> str:
    selected_name = selected.variant
    baseline_train = train["baseline"]
    baseline_validation = validation["baseline"]
    baseline_outer = outer["baseline"]
    selected_validation = validation[selected_name]
    selected_outer = outer[selected_name]
    ranked = sorted(
        (item for name, item in train.items() if name != "baseline"),
        key=lambda item: (item.net_return, item.max_drawdown),
        reverse=True,
    )
    stable = [
        item
        for item in ranked
        if validation[item.variant].net_return >= baseline_validation.net_return
        and outer[item.variant].net_return >= baseline_outer.net_return
        and validation[item.variant].max_drawdown >= baseline_validation.max_drawdown - 0.02
        and outer[item.variant].max_drawdown >= baseline_outer.max_drawdown - 0.02
    ]
    passed = (
        selected_validation.net_return >= baseline_validation.net_return
        and selected_outer.net_return >= baseline_outer.net_return
        and selected_validation.max_drawdown >= baseline_validation.max_drawdown - 0.02
        and selected_outer.max_drawdown >= baseline_outer.max_drawdown - 0.02
    )
    frozen_oos_reentries = (
        selected_validation.session_reentry_signals + selected_outer.session_reentry_signals
    )
    lines = [
        "# SOXL 切换时段恢复重入：Tick 级走步回测",
        "",
        f"生成时间：{datetime.now(UTC).isoformat()}",
        "",
        "## 研究设计",
        "",
        (
            "- 固定当前实盘基线：long-only、ATR(32) × 3.0、TE(8) ≥ 0.25、"
            "2x isolated × 62.5%（1.25x 名义敞口）。"
        ),
        "- ATR 下穿仍在下一持久化 Tick 平仓；不延迟、不屏蔽止损。",
        "- 只有退出信号位于北京时间切换点 ±30 分钟时才武装恢复重入。",
        (
            "- 从退出成交的下一根 15 分钟 K 线开始，在 2/4 根窗口内要求价格同时站上"
            "冻结的穿越前 ATR 线，并超过退出成交价 0.25/0.5/0.75 个当前 ATR；"
            "TE 过滤继续生效。"
        ),
        (
            "- 参数搜索共 9 个预注册变体；开发段选参仅使用 5–6 月。7 月为验证段，"
            "8 月为完全外样本，不参与选参。"
        ),
        (
            "- 成交采用下一 Tick、单边 5 bps 手续费和 2 bps 固定不利滑点，"
            "并计历史资金费。回放不模拟盘口深度、API 延迟或强平。"
        ),
        "",
        "## 走步区间",
        "",
        "| 阶段 | UTC 区间 | 用途 |",
        "|---|---|---|",
        "| 开发 | 2026-05-17 16:00 至 2026-06-30 23:59 | 参数选择 |",
        "| 验证 | 2026-07-01 00:00 至 2026-07-31 23:59 | 第一次冻结验证 |",
        "| 外样本 | 2026-08-01 00:00 至当前数据末端 | 第二次冻结验证 |",
        "",
        "## 冻结候选与基线",
        "",
        f"开发段在最大回撤最多比基线恶化 2 个百分点的约束下选择 `{selected_name}`。",
        "",
        "| 阶段 | 方案 | 收益 | 最大回撤 | 完整交易 | 胜率 | 恢复重入 | 手续费 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, base, candidate in (
        ("开发", baseline_train, selected),
        ("验证", baseline_validation, selected_validation),
        ("外样本", baseline_outer, selected_outer),
    ):
        lines.append(f"| {label} | baseline | " + " | ".join(result_cells(base)) + " |")
        lines.append(f"| {label} | {selected_name} | " + " | ".join(result_cells(candidate)) + " |")
    lines.extend(
        [
            "",
            "## 开发段排名前十",
            "",
            "| 排名 | 变体 | 收益 | 最大回撤 | 交易 | 胜率 | 重入 |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for index, item in enumerate(ranked[:10], 1):
        lines.append(
            f"| {index} | {item.variant} | {percentage(item.net_return)} | "
            f"{percentage(item.max_drawdown)} | {item.completed_trades} | "
            f"{percentage(item.win_rate)} | {item.session_reentry_signals} |"
        )
    lines.extend(
        [
            "",
            "## 跨段稳定候选",
            "",
            "下表只保留 7 月和 8 月收益均不低于各自基线，且最大回撤未恶化超过 2 个百分点的变体。",
            "",
            "| 变体 | 开发收益 | 7 月收益 | 8 月收益 | 7 月回撤 | 8 月回撤 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    if stable:
        for item in stable:
            val = validation[item.variant]
            out = outer[item.variant]
            lines.append(
                f"| {item.variant} | {percentage(item.net_return)} | "
                f"{percentage(val.net_return)} | {percentage(out.net_return)} | "
                f"{percentage(val.max_drawdown)} | {percentage(out.max_drawdown)} |"
            )
    else:
        lines.append("| 无 | — | — | — | — | — |")
    lines.extend(
        [
            "",
            "## 双倍成本压力",
            "",
            "压力口径为单边 10 bps 手续费和 4 bps 滑点。",
            "",
            "| 阶段 | 方案 | 收益 | 最大回撤 | 交易 | 重入 | 手续费 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, results in (("验证", stress_validation), ("外样本", stress_outer)):
        for name in ("baseline", selected_name):
            item = results[name]
            lines.append(
                f"| {label} | {name} | {percentage(item.net_return)} | "
                f"{percentage(item.max_drawdown)} | {item.completed_trades} | "
                f"{item.session_reentry_signals} | {item.total_fees:,.2f} |"
            )
    lines.extend(
        [
            "",
            "## 冻结结论",
            "",
            (
                f"自动门禁结果：**{'通过' if passed else '不通过'}**。"
                "通过要求开发段冻结候选在 7 月和 8 月都不低于基线，"
                "且任一段最大回撤不得比基线恶化超过 2 个百分点。"
            ),
            "",
            (
                "实盘部署审批：**暂不批准**。机械绩效门禁通过，但冻结候选在 7 月和 8 月合计"
                f"只有 {frozen_oos_reentries} 次"
                "恢复重入，尚不足以估计 21:30 的尾部风险。"
            ),
            "",
            (
                "本报告作为独立 paper 账户的冻结输入；paper 只做前向观察，不构成实盘批准。"
                "完成足够的影子观察后，实盘仍需单独审批。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/session_reentry/session_reentry_walk_forward_20260824.md"),
    )
    args = parser.parse_args()
    settings = load_settings()
    instrument = next(item for item in settings.instruments if item.id == "soxl_perp_long")
    parameters = parameter_grid()
    history = settings.project_root / "data/soxlusdt_history.db"
    paper = settings.project_root / "data/paper.db"

    print(f"running development grid: {len(parameters)} candidates", flush=True)
    train = run_segment(
        history,
        instrument,
        parameters,
        epoch("2026-05-17T16:00:05"),
        epoch("2026-06-30T23:59:59.999"),
    )
    selected = choose_candidate(train)
    selected_parameter = next(item for item in parameters if item.variant == selected.variant)
    print(f"selected {selected.variant}; running July validation", flush=True)
    validation = run_segment(
        history,
        instrument,
        parameters,
        epoch("2026-07-01T00:00:00"),
        epoch("2026-07-31T23:59:59.999"),
    )
    print("running August outer sample", flush=True)
    outer = run_segment(
        paper,
        instrument,
        parameters,
        epoch("2026-08-01T00:00:00"),
        None,
    )

    stress_instrument = replace(instrument, fee_bps=10.0, slippage_bps=4.0)
    stress_parameters = [parameters[0], selected_parameter]
    print("running doubled-cost stress", flush=True)
    stress_validation = run_segment(
        history,
        stress_instrument,
        stress_parameters,
        epoch("2026-07-01T00:00:00"),
        epoch("2026-07-31T23:59:59.999"),
    )
    stress_outer = run_segment(
        paper,
        stress_instrument,
        stress_parameters,
        epoch("2026-08-01T00:00:00"),
        None,
    )
    report = build_report(
        train,
        validation,
        outer,
        selected,
        stress_validation,
        stress_outer,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
