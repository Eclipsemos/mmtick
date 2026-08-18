#!/usr/bin/env python3
"""Audit high-annualized statistical-arbitrage research candidates.

This script does not fit or replay strategies. It reads frozen experiment artifacts, applies one
annualization convention, and writes a cross-experiment audit report.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "reports/experiments/high_annualized_arbitrage_candidates/2026-08-18"
DISCOVERY = ("2021-01-01T00:00:00+00:00", "2023-12-31T23:59:59.999000+00:00")
VALIDATION = ("2024-01-01T00:00:00+00:00", "2025-12-31T23:59:59.999000+00:00")
CONFIRMATION = ("2026-01-01T00:00:00+00:00", "2026-08-10T23:59:59.999000+00:00")


@dataclass(frozen=True)
class CandidateDefinition:
    candidate_id: str
    name: str
    category: str
    mechanism: str
    source: str
    configuration: str
    selection_scope: str
    independence_group: str
    shares_static_anchor: bool
    market_neutral: bool
    overfit_risk: str
    cost_note: str


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def duration_days(start: str, end: str) -> float:
    days = (parse_timestamp(end) - parse_timestamp(start)).total_seconds() / 86_400
    return round(days, 6)


def annualize(total_return: float | None, start: str, end: str) -> float | None:
    if total_return is None or total_return <= -1:
        return None
    days = duration_days(start, end)
    if days <= 0:
        raise ValueError(f"invalid interval: {start} to {end}")
    return (1 + total_return) ** (365 / days) - 1


def read_json(relative_path: str) -> dict[str, Any]:
    with (ROOT / relative_path).open(encoding="utf-8") as handle:
        return json.load(handle)


def get_path(value: dict[str, Any], dotted_path: str) -> Any:
    current: Any = value
    for part in dotted_path.split("."):
        current = current[part]
    return current


def compound_months(months: list[dict[str, Any]], prefixes: tuple[str, ...]) -> float:
    equity = 1.0
    matched = False
    for row in months:
        if str(row["label"]).startswith(prefixes):
            equity *= 1 + float(row["return"])
            matched = True
    if not matched:
        raise ValueError(f"no monthly returns matched {prefixes}")
    return equity - 1


def combine_metrics(*metrics: dict[str, Any]) -> dict[str, Any]:
    equity = 1.0
    months: list[dict[str, Any]] = []
    max_drawdown: float | None = None
    bankrupt = False
    for metric in metrics:
        equity *= 1 + float(metric["net_return"])
        months.extend(metric.get("monthly_returns", []))
        drawdown = metric.get("max_drawdown")
        if drawdown is not None:
            max_drawdown = drawdown if max_drawdown is None else min(max_drawdown, drawdown)
        bankrupt = bankrupt or bool(metric.get("bankrupt", False))
    return {
        "net_return": equity - 1,
        "max_drawdown": max_drawdown,
        "monthly_returns": months,
        "bankrupt": bankrupt,
    }


def summarize_metric(
    metric: dict[str, Any] | None, interval: tuple[str, str]
) -> dict[str, Any] | None:
    if metric is None:
        return None
    months = metric.get("monthly_returns", [])
    positive_months = sum(float(row["return"]) > 0 for row in months)
    total_return = float(metric["net_return"])
    result = {
        "start": interval[0],
        "end": interval[1],
        "days": duration_days(*interval),
        "total_return": total_return,
        "annualized_return": annualize(total_return, *interval),
        "max_drawdown": metric.get("max_drawdown"),
        "positive_months": positive_months,
        "observed_months": len(months),
        "positive_month_rate": positive_months / len(months) if months else None,
        "bankrupt": bool(metric.get("bankrupt", False)),
    }
    annualized_return = result["annualized_return"]
    max_drawdown = result["max_drawdown"]
    result["calmar_approx"] = (
        annualized_return / abs(max_drawdown)
        if annualized_return is not None and max_drawdown not in (None, 0)
        else None
    )
    return result


def pearson(left: dict[str, float], right: dict[str, float]) -> float | None:
    labels = sorted(set(left) & set(right))
    if len(labels) < 2:
        return None
    xs = [left[label] for label in labels]
    ys = [right[label] for label in labels]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)
    denominator = math.sqrt(variance_x * variance_y)
    return covariance / denominator if denominator else None


def daily_return_map(metric: dict[str, Any]) -> dict[str, float]:
    return {str(row["label"]): float(row["return"]) for row in metric.get("daily_returns", [])}


def standard_candidate(
    definition: CandidateDefinition,
    data: dict[str, Any],
    discovery_path: str | None,
    validation_path: str | None,
    confirmation_path: str,
    stress_path: str,
) -> dict[str, Any]:
    discovery = get_path(data, discovery_path) if discovery_path else None
    validation = get_path(data, validation_path) if validation_path else None
    confirmation = get_path(data, confirmation_path)
    stress = get_path(data, stress_path)
    return build_candidate(definition, discovery, validation, confirmation, stress)


def build_candidate(
    definition: CandidateDefinition,
    discovery: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    confirmation: dict[str, Any],
    stress: dict[str, Any],
) -> dict[str, Any]:
    candidate = {
        "id": definition.candidate_id,
        "name": definition.name,
        "category": definition.category,
        "mechanism": definition.mechanism,
        "source": definition.source,
        "configuration": definition.configuration,
        "selection_scope": definition.selection_scope,
        "independence_group": definition.independence_group,
        "shares_static_anchor": definition.shares_static_anchor,
        "market_neutral": definition.market_neutral,
        "fresh_forward_evidence": False,
        "overfit_risk": definition.overfit_risk,
        "cost_note": definition.cost_note,
        "discovery": summarize_metric(discovery, DISCOVERY),
        "validation": summarize_metric(validation, VALIDATION),
        "confirmation": summarize_metric(confirmation, CONFIRMATION),
        "stress_confirmation": summarize_metric(stress, CONFIRMATION),
        "_daily_returns": daily_return_map(confirmation),
    }
    confirmation_summary = candidate["confirmation"]
    stress_summary = candidate["stress_confirmation"]
    candidate["qualification"] = {
        "confirmation_annualized_over_100pct": confirmation_summary["annualized_return"] > 1,
        "stress_annualized_over_100pct": stress_summary["annualized_return"] > 1,
        "confirmation_drawdown_within_35pct": confirmation_summary["max_drawdown"] >= -0.35,
        "not_bankrupt": not confirmation_summary["bankrupt"] and not stress_summary["bankrupt"],
        "validation_annualized_over_100pct": bool(
            candidate["validation"] and candidate["validation"]["annualized_return"] > 1
        ),
    }
    candidate["qualifies_as_historical_candidate"] = all(
        candidate["qualification"][key]
        for key in (
            "confirmation_annualized_over_100pct",
            "stress_annualized_over_100pct",
            "confirmation_drawdown_within_35pct",
            "not_bankrupt",
        )
    )
    return candidate


def candidate_definitions() -> dict[str, CandidateDefinition]:
    common_cost = "5 bps fee + 2 bps slippage per fill; stress 10 + 5 bps; funding included"
    return {
        "lead_lag": CandidateDefinition(
            "btc_eth_lead_lag_dynamic",
            "BTC冲击到ETH延迟响应（动态仓位）",
            "单腿方向性统计套利",
            "BTC closed 4h shock predicts delayed ETH response; next-open execution",
            "reports/experiments/btc_eth_lead_lag/2026-08-15/"
            "btc-eth-lead-lag-20260815-090818-243225.json",
            "15d normalization, 2 sigma shock, 12x4h hold, underreaction gate; 0.5/1.5/2x",
            "960 factor configurations plus development-only sizing profiles",
            "btc_to_eth_lead_lag",
            False,
            False,
            "high",
            common_cost,
        ),
        "static3": CandidateDefinition(
            "static_three_sleeve",
            "静态三袖套事件组合",
            "方向性因子组合",
            "Lead-lag plus ETH and BTC event-continuation sleeves",
            "reports/experiments/static_factor_portfolio/2026-08-15/"
            "static-factor-portfolio-20260815-110450-461817.json",
            "50% lead-lag, 25% ETH 60d event, 25% BTC 15d event; 4x outer leverage",
            "780 three-sleeve configurations",
            "static_event_anchor",
            True,
            False,
            "high",
            common_cost,
        ),
        "static4": CandidateDefinition(
            "static_four_sleeve",
            "静态四袖套事件组合",
            "方向性因子组合",
            "Four event/lead-lag sleeves with fixed initial capital",
            "reports/experiments/static_factor_portfolio/2026-08-15/"
            "static-factor-portfolio-20260815-110450-461817.json",
            "40/15/30/15% sleeves; 4x outer leverage",
            "9,880 four-sleeve configurations",
            "static_event_anchor",
            True,
            False,
            "high",
            common_cost,
        ),
        "funding_spread": CandidateDefinition(
            "funding_spread_anchor_hybrid",
            "资金费差与四因子锚定混合",
            "相对价值与方向性混合",
            "BTC/ETH funding carry pair receives 40% beside the static directional anchor",
            "reports/experiments/funding_spread_factor/2026-08-15/"
            "funding-spread-factor-20260815-122052-685141.json",
            "6x4h lookback, 1 bps threshold, 18-bar hold, pair 0.5x, anchor 60%, outer 1.75x",
            "1,440 pair configurations and 250 anchor hybrids",
            "static_anchor_overlay",
            True,
            False,
            "high",
            common_cost + "; standalone market-neutral pair lost money in 2026",
        ),
        "funding_event": CandidateDefinition(
            "funding_event_anchor_hybrid",
            "极端资金费事件反转混合",
            "事件反转与方向性混合",
            "Long-only BTC/ETH extreme-funding reversals beside the static anchor",
            "reports/experiments/funding_event_factor/2026-08-15/"
            "funding-event-factor-20260815-123440-309171.json",
            "90-event z-score; BTC 2x4h and ETH 8x4h holds; anchor 80%, outer 1.5x",
            "5,400 event configurations plus a second-stage hybrid search",
            "static_anchor_overlay",
            True,
            False,
            "high",
            common_cost,
        ),
        "bar_momentum": CandidateDefinition(
            "btc_60d_momentum_anchor_hybrid",
            "BTC日线60日动量混合",
            "方向性因子混合",
            "Long/short BTC 60-day momentum beside the static anchor",
            "reports/experiments/bar_factor_hybrid/2026-08-15/"
            "bar-factor-hybrid-20260815-140904-249259.json",
            "10% momentum threshold; anchor 60%, factor 40%, outer 1.5x",
            "168 bar factors and 130 development-eligible hybrids",
            "static_anchor_overlay",
            True,
            False,
            "high",
            common_cost,
        ),
        "walk_forward": CandidateDefinition(
            "walk_forward_btc_anchor_hybrid",
            "Walk-forward BTC模型混合",
            "机器学习与方向性混合",
            "Annually refreshed BTC model beside the static anchor",
            "reports/experiments/walk_forward_factor/2026-08-15/"
            "walk-forward-factor-20260815-114051-794996.json",
            "60% static anchor, 40% BTC walk-forward model, 1.25x outer leverage",
            "43 eligible BTC configurations; annual model refresh and three seeds",
            "static_anchor_overlay",
            True,
            False,
            "very high",
            common_cost + "; model-only confirmation was negative",
        ),
        "market_metric": CandidateDefinition(
            "eth_crowding_anchor_hybrid",
            "ETH大户拥挤度反转混合",
            "拥挤度因子与方向性混合",
            "Fade extreme ETH top-position crowding, long-only, beside the static anchor",
            "reports/experiments/market_metric_factor/2026-08-15/"
            "market-metric-factor-20260815-135357-577775.json",
            "180x4h normalization, z=2; anchor 40%, metric 60%, outer 2.5x",
            "Multi-stage metric, threshold, allocation and leverage search",
            "static_anchor_overlay",
            True,
            False,
            "very high",
            common_cost,
        ),
        "state_overlay": CandidateDefinition(
            "eth_oi_state_anchor_overlay",
            "ETH价格/持仓交互状态缩放",
            "方向性状态缩放",
            "Prior-day ETH price/open-interest state scales the static anchor",
            "reports/experiments/market_state_overlay/2026-08-15/"
            "market-state-overlay-20260815-142603-459615.json",
            "180x4h normalization, |z|>=1 uses 2x, otherwise 1x",
            "Large state/exposure search; corrected prior-day information timing",
            "static_anchor_overlay",
            True,
            False,
            "very high",
            common_cost + "; overlay turnover costs 7 bps base and 15 bps stress",
        ),
        "state_volatility": CandidateDefinition(
            "eth_crowding_vol_target_overlay",
            "ETH拥挤度状态与波动率目标",
            "方向性状态与风险缩放",
            "Prior-day ETH crowding state and causal volatility targeting scale the anchor",
            "reports/experiments/market_state_volatility/2026-08-15/"
            "market-state-volatility-20260815-153825-882277.json",
            "state 0.8/2x; 20d RMS target 3% daily vol, 0.6-1.1x volatility layer",
            "4,320 state configurations multiplied by 36 volatility configurations",
            "static_anchor_overlay",
            True,
            False,
            "very high",
            common_cost + "; exposure-layer turnover also charged",
        ),
    }


def load_candidates() -> list[dict[str, Any]]:
    definitions = candidate_definitions()
    loaded = {key: read_json(value.source) for key, value in definitions.items()}

    lead_data = loaded["lead_lag"]
    lead_development = get_path(lead_data, "dynamic_sizing.development")
    lead_validation = {
        "net_return": compound_months(lead_development["monthly_returns"], ("2024-", "2025-")),
        "max_drawdown": None,
        "monthly_returns": [
            row
            for row in lead_development["monthly_returns"]
            if str(row["label"]).startswith(("2024-", "2025-"))
        ],
        "bankrupt": False,
    }
    candidates = [
        build_candidate(
            definitions["lead_lag"],
            None,
            lead_validation,
            get_path(lead_data, "dynamic_sizing.confirmation"),
            get_path(lead_data, "dynamic_sizing.stress_confirmation"),
        )
    ]

    static_data = loaded["static3"]
    candidates.extend(
        [
            standard_candidate(
                definitions["static3"],
                static_data,
                "best_by_sleeve_count.3.discovery",
                "best_by_sleeve_count.3.validation",
                "best_by_sleeve_count.3.confirmation",
                "best_by_sleeve_count.3.stress_confirmation",
            ),
            standard_candidate(
                definitions["static4"],
                static_data,
                "best_by_sleeve_count.4.discovery",
                "best_by_sleeve_count.4.validation",
                "best_by_sleeve_count.4.confirmation",
                "best_by_sleeve_count.4.stress_confirmation",
            ),
            standard_candidate(
                definitions["funding_spread"],
                loaded["funding_spread"],
                "selection.selected.discovery",
                "selection.selected.validation",
                "confirmation",
                "stress_confirmation",
            ),
            standard_candidate(
                definitions["funding_event"],
                loaded["funding_event"],
                "selection.selected.discovery",
                "selection.selected.validation",
                "confirmation",
                "stress_confirmation",
            ),
            standard_candidate(
                definitions["bar_momentum"],
                loaded["bar_momentum"],
                "selection.selected.discovery",
                "selection.selected.validation",
                "confirmation",
                "stress_confirmation",
            ),
        ]
    )

    walk_data = loaded["walk_forward"]
    walk_validation = combine_metrics(
        get_path(walk_data, "portfolio_selection.selected.selection_2024"),
        get_path(walk_data, "portfolio_selection.selected.selection_2025"),
    )
    candidates.append(
        build_candidate(
            definitions["walk_forward"],
            None,
            walk_validation,
            get_path(walk_data, "confirmation.portfolio"),
            get_path(walk_data, "stress_confirmation.portfolio"),
        )
    )
    candidates.extend(
        [
            standard_candidate(
                definitions["market_metric"],
                loaded["market_metric"],
                "selection.selected.discovery",
                "selection.selected.validation",
                "confirmation",
                "stress_confirmation",
            ),
            standard_candidate(
                definitions["state_overlay"],
                loaded["state_overlay"],
                "selection.selected.discovery",
                "selection.selected.validation",
                "confirmation",
                "stress_confirmation",
            ),
            standard_candidate(
                definitions["state_volatility"],
                loaded["state_volatility"],
                "selection.selected.development.discovery",
                "selection.selected.development.validation",
                "confirmation",
                "stress_confirmation",
            ),
        ]
    )
    return sorted(
        candidates, key=lambda row: row["stress_confirmation"]["annualized_return"], reverse=True
    )


def correlation_audit(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    matrix: dict[str, dict[str, float | None]] = {}
    pairs: list[dict[str, Any]] = []
    for left in candidates:
        matrix[left["id"]] = {}
        for right in candidates:
            value = pearson(left["_daily_returns"], right["_daily_returns"])
            matrix[left["id"]][right["id"]] = value
        for right in candidates:
            if left["id"] >= right["id"]:
                continue
            value = matrix[left["id"]][right["id"]]
            if value is not None:
                pairs.append({"left": left["id"], "right": right["id"], "correlation": value})
    pairs.sort(key=lambda row: row["correlation"], reverse=True)
    return {"matrix": matrix, "highest_pairs": pairs[:10], "lowest_pairs": pairs[-5:]}


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}%"


def number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_readme(payload: dict[str, Any]) -> str:
    candidates = payload["candidates"]
    qualified = [row for row in candidates if row["qualifies_as_historical_candidate"]]
    validation_passes = sum(
        row["qualification"]["validation_annualized_over_100pct"] for row in qualified
    )
    lines = [
        "# 高年化统计套利候选统一审计",
        "",
        "> 结论：找到了 10 个在 2026 复用确认区间按实际 222 天折算后，基础和压力成本",
        "> 年化均超过 100% 的**历史候选**；找到了 0 个无风险锁定套利，0 个合格的纯市场",
        "> 中性短线套利。这里的年化是短区间外推，不是已经实现的完整年度收益，也不是实盘批准。",
        "",
        "## 审计口径",
        "",
        "- 年化公式：`(1 + 区间收益)^(365 / 实际天数) - 1`。",
        "- 发现期：2021-01-01 至 2023-12-31；验证期：2024-01-01 至 2025-12-31。",
        "- 复用确认：2026-01-01 至 2026-08-10，共 222 天。该区间已被反复查看，",
        "  只能称复用确认，不能称独立前向。",
        "- 基础成本通常为每次成交 5 bps 手续费 + 2 bps 滑点；压力成本为 10 + 5 bps。",
        "  所有期货回放包含历史资金费；状态缩放策略另计换仓成本。",
        "- 候选门槛：复用确认基础年化 >100%、压力年化 >100%、确认最大回撤不差于",
        "  -35%、未破产。验证期 CAGR >100% 单独展示，不作为凑足十项的条件。",
        "",
        "## 候选排名",
        "",
        "按 2026 压力成本年化降序。`验证 CAGR` 是两年真实复合年化，不是两年累计收益。",
        "",
        "| # | 候选 | 类型 | 验证 CAGR | 2026累计 | 2026年化 | 压力累计 | "
        "压力年化 | DD | 正收益月 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(qualified, 1):
        confirmation = row["confirmation"]
        stress = row["stress_confirmation"]
        validation = row["validation"]
        positive_months = f"{confirmation['positive_months']}/{confirmation['observed_months']}"
        lines.append(
            f"| {index} | {row['name']} | {row['category']} | "
            f"{pct(validation['annualized_return'] if validation else None)} | "
            f"{pct(confirmation['total_return'])} | {pct(confirmation['annualized_return'])} | "
            f"{pct(stress['total_return'])} | {pct(stress['annualized_return'])} | "
            f"{pct(confirmation['max_drawdown'])} | {positive_months} |"
        )
    lines.extend(
        [
            "",
            f"只有 {validation_passes}/{len(qualified)} 个候选的 2024–2025 验证 CAGR 严格超过",
            "100%。因此，‘十个超过100%’只在 222 天复用确认的年化外推口径下成立；不能说",
            "十个策略都在独立的两年验证期实现了 100% 年化。",
            "",
            "## 套利属性",
            "",
            "| 层级 | 合格数量 | 说明 |",
            "|---|---:|---|",
            "| 无风险锁定套利 | 0 | 没有现货-永续基差锁定、跨所同资产价差或完整期权复制数据。 |",
            "| 纯市场中性相对价值 | 0 | BTC/ETH资金费差 pair 在2026累计约 -0.49%；"
            "高收益来自方向性锚定组合。 |",
            "| 单腿统计套利 | 1 | BTC冲击预测ETH延迟响应，但只有ETH方向腿，承担市场Beta。 |",
            "| 方向性因子组合/缩放 | 9 | 高收益依赖事件因子、趋势、拥挤度及最高约8x"
            "以上建模名义敞口。 |",
            "",
            "这意味着当前成果应叫‘高年化统计套利候选’，不应叫‘套利机会’或‘无风险套利’。",
            "",
            "## 独立性与相关性",
            "",
            "十项不是十条独立 Alpha。八项是四因子静态锚定组合本身或其缩放/混合版本；",
            "三袖套与四袖套又共享 lead-lag 和事件袖套。BTC→ETH lead-lag 是唯一没有组合",
            "其他袖套的候选，但它本身也是三/四袖套组合的主要组成部分。",
            "",
            "确认期日收益相关性最高的组合：",
            "",
            "| 候选A | 候选B | Pearson相关性 |",
            "|---|---|---:|",
        ]
    )
    names = {row["id"]: row["name"] for row in candidates}
    for pair in payload["correlation"]["highest_pairs"][:8]:
        lines.append(
            f"| {names[pair['left']]} | {names[pair['right']]} | {pair['correlation']:.3f} |"
        )
    lines.extend(
        [
            "",
            "相关性使用同一 222 天确认期的日收益计算。共同数据、共同锚定组合和共同成本模型",
            "会机械性抬高相关性；不能将这些版本等权组合后声称获得十策略分散化。",
            "",
            "## 过拟合审计",
            "",
            "| 候选 | 搜索规模/选择路径 | 风险 | 主要问题 |",
            "|---|---|---|---|",
        ]
    )
    for row in qualified:
        issue = (
            "共享四因子锚定，增量因子贡献可能很小"
            if row["shares_static_anchor"]
            else "动态仓位与因子参数均从2021–2025选择"
        )
        lines.append(
            f"| {row['name']} | {row['selection_scope']} | {row['overfit_risk']} | {issue} |"
        )
    lines.extend(
        [
            "",
            "共同风险包括：2026 已不是新鲜留出集；多轮实验存在研究者自由度；日线收盘回撤",
            "低估盘中清算风险；固定滑点不含冲击、断连和共享保证金；高年化由1月、2月、6月",
            "集中贡献，5月和7月在多数候选中亏损。",
            "",
            "## 短线套利扫描结果",
            "",
            "另行扫描了 324 个 BTC/ETH 15m、1h、4h 相对价值候选，包括价差均值回归、",
            "相对冲击延续和反转。开发期合格数为 0。最接近的 4h 冲击延续策略在基础成本下",
            "2021–2023 仅 +2.08%，2024–2025 -15.00%，2026复用确认 -6.11%；压力成本更差。",
            "这直接否定了当前 OHLCV 数据分辨率下‘高频两腿套利足以覆盖 taker 成本’的假设。",
            "详情见 `../../short_horizon_relative_value/2026-08-18/README.md`。",
            "",
            "## 结论与下一步",
            "",
            "当前能保留的是十个**观察名单版本**，而不是十个可交易套利策略。最有研究价值的",
            "独立机制是 BTC→ETH lead-lag；最值得拆解的是资金费差和拥挤度因子的 standalone",
            "增量收益。下一轮应冻结参数并等待新的完整月份，且先做以下验证：",
            "",
            "1. 从组合中剥离四因子锚定，单独报告每个增量因子的收益、换手和成本容量。",
            "2. 用逐月 expanding walk-forward 形成真正未查看的前向账本，不再用2026回选。",
            "3. 补齐现货、永续、盘口与跨所时间同步数据后，才研究基差锁定和执行级套利。",
            "4. 对最高名义敞口、盘中回撤、资金费突变、滑点3倍和延迟一根K线做破坏性测试。",
            "",
            "本报告仅用于 research-only 分支，不批准模拟盘或实盘。机器可读结果见",
            "[`results.json`](results.json)。",
            "",
        ]
    )
    return "\n".join(lines)


def build_payload() -> dict[str, Any]:
    candidates = load_candidates()
    correlations = correlation_audit(candidates)
    for candidate in candidates:
        candidate.pop("_daily_returns")
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "title": "High-annualized statistical-arbitrage candidate audit",
        "annualization_formula": "(1 + interval_return) ** (365 / actual_days) - 1",
        "periods": {
            "discovery": {"start": DISCOVERY[0], "end": DISCOVERY[1]},
            "validation": {"start": VALIDATION[0], "end": VALIDATION[1]},
            "reused_confirmation": {"start": CONFIRMATION[0], "end": CONFIRMATION[1]},
        },
        "fresh_forward_candidate_count": 0,
        "locked_arbitrage_candidate_count": 0,
        "pure_market_neutral_qualified_count": 0,
        "candidates": candidates,
        "correlation": correlations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    (output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(render_readme(payload), encoding="utf-8")
    qualified = sum(row["qualifies_as_historical_candidate"] for row in payload["candidates"])
    print(f"wrote {qualified} qualifying candidates to {output_dir}")


if __name__ == "__main__":
    main()
