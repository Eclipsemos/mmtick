"""Frozen forward monitor for the BTC/ETH 4h reversal factors."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import ResearchBar, aggregate_bars, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_stability import (
    FactorStabilityConfig,
    causal_market_regimes,
    cost_aware_forward_returns,
    spearman_ic,
)

ASSETS = ("btc_perp", "eth_perp")
DAY_MS = 86_400_000


@dataclass(frozen=True)
class FrozenFactor:
    factor: str
    horizon_bars: int
    orientation: int
    source_asset: str
    target_asset: str


@dataclass(frozen=True)
class FrozenFactorMonitor:
    id: str
    status: str
    evidence_lock_date: date
    forward_evidence_start_date: date
    source_report: str
    parameter_hash: str
    bar_interval_minutes: int
    factors: tuple[FrozenFactor, ...]
    fee_bps_per_fill: float
    slippage_bps_per_fill: float
    development_ic: dict[str, float]
    minimum_complete_days: int
    minimum_samples: int
    minimum_cost_ic: float
    minimum_retention: float


def load_frozen_factor_monitor(path: Path) -> FrozenFactorMonitor:
    """Load and strictly validate the immutable forward protocol."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported factor monitor schema")
    if payload.get("strategy_family") != "single_factor_ic_monitor":
        raise ValueError("candidate is not a single-factor IC monitor")
    if payload.get("approved_for_trading") is not False:
        raise ValueError("factor monitor must not be approved for trading")
    parameters = payload["parameters"]
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    if digest != payload.get("parameter_hash"):
        raise ValueError("factor monitor parameter hash mismatch")
    factors = tuple(FrozenFactor(**item) for item in parameters["factors"])
    expected = (
        FrozenFactor("own_ret_6", 1, -1, "btc_perp", "btc_perp"),
        FrozenFactor("other_ret_6", 1, -1, "btc_perp", "eth_perp"),
    )
    if parameters.get("bar_interval_minutes") != 240 or factors != expected:
        raise ValueError("factor monitor parameters differ from the frozen v1 protocol")
    lock_date = date.fromisoformat(payload["evidence_lock_date"])
    forward_start = date.fromisoformat(payload["forward_evidence_start_date"])
    if forward_start != lock_date + timedelta(days=1):
        raise ValueError("forward evidence must begin after the evidence lock")
    costs = payload["costs"]
    gates = payload["forward_gates"]
    if costs != {
        "fee_bps_per_fill": 5.0,
        "slippage_bps_per_fill": 2.0,
        "funding_included": True,
    }:
        raise ValueError("factor monitor costs differ from the frozen v1 protocol")
    expected_gates = {
        "minimum_complete_days_for_review": 30,
        "minimum_non_overlapping_samples_for_review": 150,
        "minimum_cost_adjusted_ic": 0.02,
        "minimum_development_ic_retention": 0.5,
    }
    if gates != expected_gates:
        raise ValueError("factor monitor gates differ from the frozen v1 protocol")
    development = payload["development_reference"]
    return FrozenFactorMonitor(
        id=payload["id"],
        status=payload["status"],
        evidence_lock_date=lock_date,
        forward_evidence_start_date=forward_start,
        source_report=payload["source_report"],
        parameter_hash=digest,
        bar_interval_minutes=240,
        factors=factors,
        fee_bps_per_fill=5.0,
        slippage_bps_per_fill=2.0,
        development_ic={asset: float(development[asset]["cost_adjusted_ic"]) for asset in ASSETS},
        minimum_complete_days=30,
        minimum_samples=150,
        minimum_cost_ic=0.02,
        minimum_retention=0.5,
    )


def load_factor_forward_market(
    database: Path, monitor: FrozenFactorMonitor
) -> tuple[dict[str, list[ResearchBar]], dict[str, list[list[Any]]]]:
    """Load aligned bars and funding through a read-only database connection."""
    loaded = {asset: load_market(database, asset) for asset in ASSETS}
    bars = {
        asset: aggregate_bars(loaded[asset][0], monitor.bar_interval_minutes) for asset in ASSETS
    }
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
    _require_aligned(bars)
    return bars, funding


def evaluate_frozen_factor_forward(
    monitor: FrozenFactorMonitor,
    bars: dict[str, list[ResearchBar]],
    funding: dict[str, list[list[Any]]],
) -> dict[str, Any]:
    """Evaluate post-lock records only, without selecting any parameter."""
    np = _numpy()
    _require_aligned(bars)
    config = FactorStabilityConfig()
    regimes = {asset: causal_market_regimes(np, bars[asset], config) for asset in ASSETS}
    factor_outputs = {}
    for frozen in monitor.factors:
        source_values = _lag_returns(np, bars[frozen.source_asset], 6) * frozen.orientation
        labels = cost_aware_forward_returns(
            np,
            bars[frozen.target_asset],
            funding[frozen.target_asset],
            frozen.horizon_bars,
            monitor.fee_bps_per_fill,
            monitor.slippage_bps_per_fill,
        )
        factor_outputs[frozen.target_asset] = _evaluate_factor(
            np,
            monitor,
            frozen,
            bars[frozen.target_asset],
            source_values,
            labels,
            regimes[frozen.target_asset],
        )
    review_ready = all(output["gates"]["sample_ready"] for output in factor_outputs.values())
    all_passed = review_ready and all(
        output["gates"]["minimum_cost_adjusted_ic"]
        and output["gates"]["minimum_development_ic_retention"]
        for output in factor_outputs.values()
    )
    complete_days = min((output["complete_days"] for output in factor_outputs.values()), default=0)
    return {
        "schema_version": 1,
        "id": f"{monitor.id}-monitor",
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate_id": monitor.id,
        "candidate_status": monitor.status,
        "approved_for_trading": False,
        "parameter_hash": monitor.parameter_hash,
        "parameter_search_performed": False,
        "evidence_lock_date": monitor.evidence_lock_date.isoformat(),
        "forward_evidence_start_date": monitor.forward_evidence_start_date.isoformat(),
        "source_report": monitor.source_report,
        "data_through": _timestamp(bars[ASSETS[0]][-1].end_ms),
        "complete_forward_days": complete_days,
        "status": (
            "review_passed_observation_only"
            if all_passed
            else "review_failed"
            if review_ready
            else "collecting_forward_evidence"
            if complete_days
            else "awaiting_forward_data"
        ),
        "factors": factor_outputs,
        "transformer_combination_allowed": False,
        "message": (
            "Frozen factors passed the interim IC review but still require a genuinely new "
            "complete month before combination."
            if all_passed
            else "Frozen factors failed the interim IC review."
            if review_ready
            else "Continue collecting data without changing factors, orientation, or gates."
        ),
    }


def write_factor_forward_report(payload: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{payload['id']}.json"
    markdown_path = output_dir / f"{payload['id']}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_factor_forward_markdown(payload), encoding="utf-8")
    return json_path, markdown_path


def render_factor_forward_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BTC/ETH 4h Reversal Factor Forward Monitor",
        "",
        f"Generated: {payload['generated_at']}",
        f"Evidence lock: `{payload['evidence_lock_date']} UTC`",
        f"Forward start: `{payload['forward_evidence_start_date']} UTC`",
        f"Data through: `{payload['data_through']}`",
        f"Parameter hash: `{payload['parameter_hash']}`",
        f"Status: `{payload['status']}`",
        "Parameter search: no",
        "Trading approved: no",
        "",
        (
            "| Target | Complete days | Samples | Raw IC | Cost-adjusted IC | "
            "Retention | Review ready |"
        ),
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for asset, result in payload["factors"].items():
        lines.append(
            f"| {asset} | {result['complete_days']} | {result['samples']} | "
            f"{_format_ic(result['raw_ic'])} | {_format_ic(result['cost_adjusted_ic'])} | "
            f"{_format_percent(result['development_ic_retention'])} | "
            f"{'yes' if result['gates']['sample_ready'] else 'no'} |"
        )
    for asset, result in payload["factors"].items():
        lines.extend(["", f"## {asset} Daily IC", ""])
        lines.extend(["| UTC date | Samples | Cost-adjusted IC |", "|---|---:|---:|"])
        for row in result["daily"]:
            lines.append(
                f"| {row['date']} | {row['samples']} | {_format_ic(row['cost_adjusted_ic'])} |"
            )
    lines.extend(
        [
            "",
            "Transformer combination remains disabled. This report cannot change the frozen",
            "factor definitions, orientation, costs, or review gates.",
            "",
        ]
    )
    return "\n".join(lines)


def _evaluate_factor(
    np: Any,
    monitor: FrozenFactorMonitor,
    frozen: FrozenFactor,
    bars: list[ResearchBar],
    scores: Any,
    labels: dict[str, Any],
    regimes: list[str | None],
) -> dict[str, Any]:
    grouped: dict[date, list[dict[str, Any]]] = {}
    for index, bar in enumerate(bars):
        day = datetime.fromtimestamp(bar.start_ms / 1000, UTC).date()
        endpoint = index + frozen.horizon_bars + 1
        if day < monitor.forward_evidence_start_date or endpoint >= len(bars):
            continue
        if not all(
            np.isfinite(value)
            for value in (scores[index], labels["raw"][index], labels["cost_adjusted"][index])
        ):
            continue
        grouped.setdefault(day, []).append(
            {
                "start_ms": bar.start_ms,
                "score": float(scores[index]),
                "raw": float(labels["raw"][index]),
                "cost": float(labels["cost_adjusted"][index]),
                "regime": regimes[index],
            }
        )
    bars_per_day = 24 * 60 // monitor.bar_interval_minutes
    complete = []
    expected = monitor.forward_evidence_start_date
    while True:
        rows = grouped.get(expected, [])
        expected_starts = [
            _day_start_ms(expected) + offset * monitor.bar_interval_minutes * 60_000
            for offset in range(bars_per_day)
        ]
        if len(rows) != bars_per_day or [row["start_ms"] for row in rows] != expected_starts:
            break
        complete.append((expected, rows))
        expected += timedelta(days=1)
    records = [row for _day, rows in complete for row in rows]
    raw_ic = spearman_ic([row["score"] for row in records], [row["raw"] for row in records])
    cost_ic = spearman_ic([row["score"] for row in records], [row["cost"] for row in records])
    development_ic = monitor.development_ic[frozen.target_asset]
    retention = cost_ic / development_ic if cost_ic is not None else None
    sample_ready = (
        len(complete) >= monitor.minimum_complete_days and len(records) >= monitor.minimum_samples
    )
    daily = [
        {
            "date": day.isoformat(),
            "samples": len(rows),
            "raw_ic": spearman_ic([row["score"] for row in rows], [row["raw"] for row in rows]),
            "cost_adjusted_ic": spearman_ic(
                [row["score"] for row in rows], [row["cost"] for row in rows]
            ),
        }
        for day, rows in complete
    ]
    regime_metrics = {}
    for name in sorted({str(row["regime"]) for row in records if row["regime"] is not None}):
        subset = [row for row in records if row["regime"] == name]
        regime_metrics[name] = {
            "samples": len(subset),
            "cost_adjusted_ic": spearman_ic(
                [row["score"] for row in subset], [row["cost"] for row in subset]
            ),
        }
    return {
        "factor": frozen.factor,
        "source_asset": frozen.source_asset,
        "target_asset": frozen.target_asset,
        "orientation": frozen.orientation,
        "horizon_bars": frozen.horizon_bars,
        "complete_days": len(complete),
        "first_day": complete[0][0].isoformat() if complete else None,
        "last_day": complete[-1][0].isoformat() if complete else None,
        "samples": len(records),
        "raw_ic": raw_ic,
        "cost_adjusted_ic": cost_ic,
        "development_cost_adjusted_ic": development_ic,
        "development_ic_retention": retention,
        "daily": daily,
        "regimes": regime_metrics,
        "gates": {
            "minimum_complete_days": len(complete) >= monitor.minimum_complete_days,
            "minimum_samples": len(records) >= monitor.minimum_samples,
            "sample_ready": sample_ready,
            "minimum_cost_adjusted_ic": (
                cost_ic >= monitor.minimum_cost_ic if sample_ready and cost_ic is not None else None
            ),
            "minimum_development_ic_retention": (
                retention >= monitor.minimum_retention
                if sample_ready and retention is not None
                else None
            ),
        },
    }


def _lag_returns(np: Any, bars: list[ResearchBar], lag: int) -> Any:
    closes = np.array([float(bar.close) for bar in bars], dtype=np.float64)
    result = np.full(len(bars), np.nan, dtype=np.float64)
    result[lag:] = closes[lag:] / closes[:-lag] - 1.0
    return result


def _require_aligned(bars: dict[str, list[ResearchBar]]) -> None:
    if set(bars) != set(ASSETS) or not bars[ASSETS[0]]:
        raise ValueError("factor forward monitor requires BTC and ETH bars")
    if len(bars[ASSETS[0]]) != len(bars[ASSETS[1]]):
        raise ValueError("factor forward BTC and ETH bars differ in length")
    if any(
        left.start_ms != right.start_ms
        for left, right in zip(bars[ASSETS[0]], bars[ASSETS[1]], strict=True)
    ):
        raise ValueError("factor forward BTC and ETH bars are not aligned")


def _numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("factor forward monitoring requires numpy") from exc
    return np


def _day_start_ms(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def _format_ic(value: float | None) -> str:
    return "-" if value is None else f"{value:+.4f}"


def _format_percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"
