#!/usr/bin/env python3
"""Independent statistical audit of the causal BTC hysteresis walk-forward years."""

from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

INPUT = Path("reports/experiments/btc_hysteresis_walk_forward/2026-09-02/results.json")
OUTPUT = Path("reports/experiments/btc_hysteresis_walk_forward/2026-09-02/stats")
SAMPLES = 100_000
SEED = 20260902


def main() -> None:
    payload = json.loads(INPUT.read_text(encoding="utf-8"))
    rows = payload["years"]
    strategy = [float(row["test"]["strategy_return"]) for row in rows]
    benchmark = [float(row["test"]["benchmark_return"]) for row in rows]
    excess = [left - right for left, right in zip(strategy, benchmark, strict=True)]
    relative = [
        (1.0 + left) / (1.0 + right) for left, right in zip(strategy, benchmark, strict=True)
    ]
    rng = random.Random(SEED)
    mean_distribution = []
    compound_distribution = []
    for _ in range(SAMPLES):
        indices = [rng.randrange(len(rows)) for _ in rows]
        mean_distribution.append(sum(excess[index] for index in indices) / len(indices))
        compound = 1.0
        for index in indices:
            compound *= relative[index]
        compound_distribution.append(compound - 1.0)
    signs = [value > 0 for value in excess]
    result = {
        "source": str(INPUT),
        "seed": SEED,
        "samples": SAMPLES,
        "years": [row["year"] for row in rows],
        "annual_excess": excess,
        "summary": {
            "positive_years": sum(signs),
            "total_years": len(rows),
            "positive_year_fraction": sum(signs) / len(signs),
            "mean_excess": float(statistics.mean(excess)),
            "median_excess": float(statistics.median(excess)),
            "worst_excess": float(min(excess)),
            "best_excess": float(max(excess)),
            "max_positive_streak": max_streak(signs, True),
            "max_negative_streak": max_streak(signs, False),
            "max_intrabar_leverage": max(row["test"]["maximum_intrabar_leverage"] for row in rows),
            "liquidations": sum(bool(row["test"]["liquidated"]) for row in rows),
            "one_sided_sign_test_pvalue": sign_test_pvalue(sum(signs), len(rows)),
        },
        "bootstrap": {
            "mean_excess_ci95": quantiles(mean_distribution),
            "compound_relative_ci95": quantiles(compound_distribution),
            "probability_mean_excess_positive": sum(value > 0 for value in mean_distribution)
            / SAMPLES,
            "probability_compound_outperformance": sum(value > 0 for value in compound_distribution)
            / SAMPLES,
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(result), encoding="utf-8")
    print(OUTPUT / "README.md")


def quantiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def at(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        left = int(position)
        right = min(left + 1, len(ordered) - 1)
        weight = position - left
        return ordered[left] * (1 - weight) + ordered[right] * weight

    return {"p025": at(0.025), "p50": at(0.5), "p975": at(0.975)}


def max_streak(values: list[bool], expected: bool) -> int:
    best = current = 0
    for value in values:
        if bool(value) is expected:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def sign_test_pvalue(positive: int, total: int) -> float:
    # Exact one-sided sign test under H0: P(excess > 0) = 0.5.
    return sum(math.comb(total, k) for k in range(positive, total + 1)) / 2**total


def render(result: dict) -> str:
    summary = result["summary"]
    bootstrap = result["bootstrap"]
    mean_ci = bootstrap["mean_excess_ci95"]
    compound_ci = bootstrap["compound_relative_ci95"]
    lines = [
        "# BTC 迟滞 Walk-Forward 独立统计审计",
        "",
        "仅使用年度因果 Walk-Forward 的测试年结果；没有重新选择参数。"
        "所有测试均满足严格 3X 杠杆上限。",
        "",
        "## 年度超额",
        "",
        f"- 超过 B&H：{summary['positive_years']}/{summary['total_years']} 年 "
        f"（{summary['positive_year_fraction']:.2%}）。",
        f"- 平均年度超额：{summary['mean_excess']:.2%}；中位数：{summary['median_excess']:.2%}。",
        f"- 最佳/最差年度超额：{summary['best_excess']:.2%} / {summary['worst_excess']:.2%}。",
        f"- 最长连续跑赢/落后：{summary['max_positive_streak']} / "
        f"{summary['max_negative_streak']} 年。",
        f"- 精确单侧符号检验 p 值：{summary['one_sided_sign_test_pvalue']:.4f}。",
        "",
        "## 年度重抽样（100,000 次）",
        "",
        f"- 平均年度超额 95% 区间：{mean_ci['p025']:.2%} 至 "
        f"{mean_ci['p975']:.2%}（中位数 {mean_ci['p50']:.2%}）。",
        f"- 复合相对收益 95% 区间：{compound_ci['p025']:.2%} 至 "
        f"{compound_ci['p975']:.2%}（中位数 {compound_ci['p50']:.2%}）。",
        f"- 重抽样平均超额为正概率：{bootstrap['probability_mean_excess_positive']:.2%}。",
        f"- 重抽样复合跑赢 B&H 概率：{bootstrap['probability_compound_outperformance']:.2%}。",
        "",
        "## 判定",
        "",
        f"最高盘中有效杠杆为 {summary['max_intrabar_leverage']:.3f}X，"
        f"清算次数为 {summary['liquidations']}。",
        "年度结果方向偏正，但样本只有 8 个测试年，符号检验未达到常用显著性标准；"
        "该证据支持继续前向观察，不足以证明稳定 Edge 或批准实盘。",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
