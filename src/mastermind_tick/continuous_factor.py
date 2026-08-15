"""Causal cross-asset features for continuous bar-level factor models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from mastermind_tick.bar_research import ResearchBar

FEATURE_NAMES = (
    "own_ret_1",
    "own_ret_3",
    "own_ret_6",
    "own_ret_18",
    "own_ret_42",
    "other_ret_1",
    "other_ret_3",
    "other_ret_6",
    "other_ret_18",
    "other_ret_42",
    "relative_ret_1",
    "relative_ret_6",
    "relative_ret_18",
    "own_vol_6",
    "own_vol_18",
    "own_vol_42",
    "other_vol_6",
    "other_vol_18",
    "other_vol_42",
    "return_corr_18",
    "return_corr_42",
    "own_range_6",
    "other_range_6",
    "own_volume_ratio_18",
    "other_volume_ratio_18",
    "own_trend_6",
    "own_trend_18",
    "own_trend_42",
    "other_trend_6",
    "other_trend_18",
    "other_trend_42",
    "own_funding",
    "other_funding",
    "own_funding_6",
    "other_funding_6",
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
)


@dataclass(frozen=True)
class ContinuousSignalCandidate:
    horizon_bars: int
    direction: str
    threshold: float
    threshold_quantile: float
    smoothing_bars: int
    minimum_hold_bars: int
    confirmation_bars: int
    exposure: Decimal = Decimal("1")
    monthly_loss_limit: Decimal | None = None

    def __post_init__(self) -> None:
        if self.horizon_bars < 1:
            raise ValueError("continuous factor horizon must be positive")
        if self.direction not in {"long_only", "long_short"}:
            raise ValueError("continuous factor direction is unsupported")
        if self.threshold < 0 or not 0 < self.threshold_quantile < 1:
            raise ValueError("continuous factor threshold is invalid")
        if self.smoothing_bars < 1 or self.minimum_hold_bars < 1:
            raise ValueError("continuous factor smoothing and hold must be positive")
        if self.confirmation_bars < 1:
            raise ValueError("continuous factor confirmation must be positive")
        if self.exposure <= 0 or self.exposure > Decimal("10"):
            raise ValueError("continuous factor exposure must be between zero and ten")
        if self.monthly_loss_limit is not None and not Decimal(
            "0"
        ) < self.monthly_loss_limit < Decimal("1"):
            raise ValueError("continuous factor monthly loss limit is invalid")

    @property
    def id(self) -> str:
        direction = "long" if self.direction == "long_only" else "long-short"
        quantile = f"{self.threshold_quantile:.2f}".replace(".", "p")
        exposure = f"{self.exposure:g}".replace(".", "p")
        loss = (
            "none"
            if self.monthly_loss_limit is None
            else f"{self.monthly_loss_limit:g}".replace(".", "p")
        )
        return (
            f"continuous-{direction}-h{self.horizon_bars}-q{quantile}"
            f"-smooth{self.smoothing_bars}-hold{self.minimum_hold_bars}"
            f"-confirm{self.confirmation_bars}-exp{exposure}-loss{loss}"
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "id": self.id,
                "exposure": float(self.exposure),
                "monthly_loss_limit": (
                    float(self.monthly_loss_limit) if self.monthly_loss_limit is not None else None
                ),
            }
        )
        return payload


def cross_asset_features(
    np: Any,
    own_bars: list[ResearchBar],
    other_bars: list[ResearchBar],
    own_funding: list[list[Any]],
    other_funding: list[list[Any]],
) -> Any:
    """Return point-in-time features; row i only reads bars and funding through i."""
    _validate_aligned(own_bars, other_bars, own_funding, other_funding)
    own = _series(np, own_bars, own_funding)
    other = _series(np, other_bars, other_funding)
    hour = np.array([(bar.start_ms // 3_600_000) % 24 for bar in own_bars], dtype=np.float64)
    weekday = np.array([(bar.start_ms // 86_400_000 + 3) % 7 for bar in own_bars], dtype=np.float64)
    matrix = np.column_stack(
        (
            *own["lag_returns"],
            *other["lag_returns"],
            own["returns"] - other["returns"],
            own["lag_returns"][2] - other["lag_returns"][2],
            own["lag_returns"][3] - other["lag_returns"][3],
            *own["volatility"],
            *other["volatility"],
            _rolling_correlation(np, own["returns"], other["returns"], 18),
            _rolling_correlation(np, own["returns"], other["returns"], 42),
            own["range"],
            other["range"],
            own["volume_ratio"],
            other["volume_ratio"],
            *own["trend"],
            *other["trend"],
            own["funding"],
            other["funding"],
            _rolling_mean(np, own["funding"], 6),
            _rolling_mean(np, other["funding"], 6),
            np.sin(2 * np.pi * hour / 24),
            np.cos(2 * np.pi * hour / 24),
            np.sin(2 * np.pi * weekday / 7),
            np.cos(2 * np.pi * weekday / 7),
        )
    )
    if matrix.shape[1] != len(FEATURE_NAMES):
        raise RuntimeError("continuous factor feature definition is inconsistent")
    return matrix


def forward_open_returns(np: Any, bars: list[ResearchBar], horizon_bars: int) -> Any:
    """Label a closed bar with the return from next open to a later open."""
    if horizon_bars < 1:
        raise ValueError("continuous factor horizon must be positive")
    result = np.full(len(bars), np.nan, dtype=np.float64)
    opens = np.array([float(bar.open) for bar in bars], dtype=np.float64)
    if len(bars) > horizon_bars + 1:
        end = len(bars) - horizon_bars - 1
        result[:end] = opens[horizon_bars + 1 :] / opens[1 : end + 1] - 1.0
    return result


def managed_targets(
    scores: list[float | None], candidate: ContinuousSignalCandidate
) -> tuple[Decimal | None, ...]:
    """Convert frozen predictions into causal, stateful target exposure."""
    state = 0
    hold_count = 0
    pending = 0
    pending_count = 0
    ema: float | None = None
    alpha = 1.0 if candidate.smoothing_bars == 1 else 2.0 / (candidate.smoothing_bars + 1)
    result: list[Decimal | None] = []
    for score in scores:
        if score is None:
            result.append(None)
            continue
        ema = score if ema is None else ema + alpha * (score - ema)
        desired = (
            1
            if ema >= candidate.threshold
            else -1
            if candidate.direction == "long_short" and ema <= -candidate.threshold
            else 0
        )
        if state:
            hold_count += 1
            if desired == state:
                pending = 0
                pending_count = 0
            elif hold_count >= candidate.minimum_hold_bars:
                state = 0
                hold_count = 0
                pending = 0
                pending_count = 0
        if not state and desired:
            pending_count = pending_count + 1 if pending == desired else 1
            pending = desired
            if pending_count >= candidate.confirmation_bars:
                state = desired
                hold_count = 0
                pending = 0
                pending_count = 0
        result.append(Decimal(state) * candidate.exposure if score is not None else None)
    return tuple(result)


def _series(np: Any, bars: list[ResearchBar], funding: list[list[Any]]) -> dict[str, Any]:
    close = np.array([float(bar.close) for bar in bars], dtype=np.float64)
    high = np.array([float(bar.high) for bar in bars], dtype=np.float64)
    low = np.array([float(bar.low) for bar in bars], dtype=np.float64)
    volume = np.array([float(bar.volume) for bar in bars], dtype=np.float64)
    returns = _lag_return(np, close, 1)
    return {
        "returns": returns,
        "lag_returns": tuple(_lag_return(np, close, lag) for lag in (1, 3, 6, 18, 42)),
        "volatility": tuple(_rolling_std(np, returns, window) for window in (6, 18, 42)),
        "range": _rolling_mean(np, (high - low) / close, 6),
        "volume_ratio": np.log1p(volume) - np.log1p(_rolling_mean(np, volume, 18)),
        "trend": tuple(close / _rolling_mean(np, close, window) - 1.0 for window in (6, 18, 42)),
        "funding": np.array(
            [float(sum((event.rate for event in row), Decimal("0"))) for row in funding],
            dtype=np.float64,
        ),
    }


def _lag_return(np: Any, values: Any, lag: int) -> Any:
    result = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) > lag:
        result[lag:] = values[lag:] / values[:-lag] - 1.0
    return result


def _rolling_mean(np: Any, values: Any, window: int) -> Any:
    result = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) < window:
        return result
    valid = np.isfinite(values).astype(np.int64)
    total = np.cumsum(np.nan_to_num(values, nan=0.0))
    count = np.cumsum(valid)
    sums = total[window - 1 :] - np.r_[0.0, total[:-window]]
    counts = count[window - 1 :] - np.r_[0, count[:-window]]
    result[window - 1 :] = np.divide(sums, counts, out=np.full_like(sums, np.nan), where=counts > 0)
    return result


def _rolling_std(np: Any, values: Any, window: int) -> Any:
    mean = _rolling_mean(np, values, window)
    squared = _rolling_mean(np, values * values, window)
    return np.sqrt(np.maximum(squared - mean * mean, 0.0))


def _rolling_correlation(np: Any, left: Any, right: Any, window: int) -> Any:
    left_mean = _rolling_mean(np, left, window)
    right_mean = _rolling_mean(np, right, window)
    covariance = _rolling_mean(np, left * right, window) - left_mean * right_mean
    denominator = _rolling_std(np, left, window) * _rolling_std(np, right, window)
    return np.divide(
        covariance,
        denominator,
        out=np.full(len(left), np.nan, dtype=np.float64),
        where=denominator > 1e-12,
    )


def _validate_aligned(
    own_bars: list[ResearchBar],
    other_bars: list[ResearchBar],
    own_funding: list[list[Any]],
    other_funding: list[list[Any]],
) -> None:
    lengths = {len(own_bars), len(other_bars), len(own_funding), len(other_funding)}
    if len(lengths) != 1 or not own_bars:
        raise ValueError("continuous factor inputs have different or empty lengths")
    if any(own.start_ms != other.start_ms for own, other in zip(own_bars, other_bars, strict=True)):
        raise ValueError("continuous factor BTC/ETH bars are not aligned")
