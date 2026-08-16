"""Forward-only paper ledger for the frozen BTC/ETH calendar router."""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import csv
import io
import json
import time
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from mastermind_tick.calendar_router_model import (
    apply_state_volatility_overlay,
    attach_events,
    combine_calendar_route,
    combine_static_anchor,
    replay_independent_sleeve,
)
from mastermind_tick.config import PortfolioPaperSettings
from mastermind_tick.feeds import BINANCE_FUTURES_REST
from mastermind_tick.models import Bar, FundingRate, FuturesMetricBar
from mastermind_tick.store import PaperStore

DAY_MS = 86_400_000
FOUR_HOUR_MS = 14_400_000
DATA_VERSION = "binance-public-full-warmup-v4"
IMPLEMENTATION_VERSION = "calendar-router-forward-v4"
METRIC_ARCHIVE_BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
METRIC_REST_BASE = "https://fapi.binance.com/futures/data"
FORWARD_START_MS = int(datetime(2026, 8, 16, tzinfo=UTC).timestamp() * 1000)
REPLAY_START_MS = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
METRIC_WINDOW = 540
SHOCK_WINDOW_60D = 360
PRE_REPLAY_4H_BARS = SHOCK_WINDOW_60D + 2
DAILY_WARMUP_BARS = 64
FOUR_HOUR_HISTORY_START_MS = REPLAY_START_MS - METRIC_WINDOW * FOUR_HOUR_MS
DAILY_HISTORY_START_MS = 0
DAILY_WARMUP_START_MS = REPLAY_START_MS - DAILY_WARMUP_BARS * DAY_MS


@dataclass
class RouterRuntime:
    settings: PortfolioPaperSettings
    store: PaperStore
    initial_cash: Decimal
    status: str = "STARTING"
    status_message: str = "Loading frozen strategy and warm-up data"
    last_update_ms: int | None = None
    last_day: str | None = None
    error_count: int = 0
    task: asyncio.Task[None] | None = None
    input_validation: str = "PENDING"
    input_validation_details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.definition = json.loads(self.settings.strategy_path.read_text(encoding="utf-8"))

    async def start(self) -> None:
        now_ms = int(time.time() * 1000)
        self.store.ensure_portfolio_account(
            self.settings.id,
            self.settings.symbol,
            self.settings.display_symbol,
            self.settings.venue,
            self.settings.currency,
            float(self.initial_cash),
            now_ms,
        )
        self.task = asyncio.create_task(self._run(), name=f"portfolio-{self.settings.id}")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.status = "STOPPED"
        self.status_message = "Service stopped"

    async def _run(self) -> None:
        while True:
            try:
                await self.update()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error_count += 1
                self.status = "DEGRADED"
                self.input_validation = "FAILED"
                self.status_message = f"Daily refresh failed: {type(exc).__name__}: {exc}"
                self.store.add_event(
                    self.settings.id,
                    int(time.time() * 1000),
                    "ERROR",
                    "PORTFOLIO_REFRESH_FAILED",
                    self.status_message,
                )
            await asyncio.sleep(self.settings.poll_seconds)

    async def update(self) -> None:
        now_ms = int(time.time() * 1000)
        btc_4h, eth_4h, btc_daily, eth_daily, btc_funding, eth_funding = await asyncio.gather(
            _klines_from("BTCUSDT", "4h", FOUR_HOUR_MS, FOUR_HOUR_HISTORY_START_MS),
            _klines_from("ETHUSDT", "4h", FOUR_HOUR_MS, FOUR_HOUR_HISTORY_START_MS),
            _klines_from("BTCUSDT", "1d", DAY_MS, DAILY_HISTORY_START_MS),
            _klines_from("ETHUSDT", "1d", DAY_MS, DAILY_HISTORY_START_MS),
            _funding("BTCUSDT", REPLAY_START_MS, now_ms),
            _funding("ETHUSDT", REPLAY_START_MS, now_ms),
        )
        validation = _validate_replay_inputs(btc_4h, eth_4h, btc_daily, eth_daily)
        self.store.upsert_history_bars(_market("btc_perp", "BTCUSDT"), 240, btc_4h, DATA_VERSION)
        self.store.upsert_history_bars(_market("eth_perp", "ETHUSDT"), 240, eth_4h, DATA_VERSION)
        self.store.upsert_history_bars(
            _market("btc_perp", "BTCUSDT"), 1440, btc_daily, DATA_VERSION
        )
        self.store.upsert_history_bars(
            _market("eth_perp", "ETHUSDT"), 1440, eth_daily, DATA_VERSION
        )
        for market_id, symbol, values in (
            ("btc_perp", "BTCUSDT", btc_funding),
            ("eth_perp", "ETHUSDT", eth_funding),
        ):
            for funding in values:
                self.store.record_funding_rate(market_id, symbol, funding, source=DATA_VERSION)

        await _refresh_metric_history(self.store, "ETHUSDT", now_ms)
        eth_metrics = self.store.futures_metric_bars(
            "ETHUSDT", 240, eth_4h[0].start_ms, eth_4h[-1].end_ms
        )
        validation["eth_metrics"] = _validate_metric_warmup(eth_4h, eth_metrics)
        self.input_validation_details = validation
        self.input_validation = "COMPLETE"

        existing = {
            (row["ledger"], row["day"])
            for ledger in ("base", "stress")
            for row in self.store.portfolio_ledger(self.settings.id, ledger)
        }
        ledgers = {
            name: self.store.portfolio_ledger(self.settings.id, name) for name in ("base", "stress")
        }
        for name, costs in (
            ("base", (Decimal("5"), Decimal("2"), Decimal("7"))),
            ("stress", (Decimal("10"), Decimal("5"), Decimal("15"))),
        ):
            days = _daily_states(
                self.definition,
                btc_4h,
                eth_4h,
                btc_daily,
                eth_daily,
                btc_funding,
                eth_funding,
                eth_metrics,
                *costs,
            )
            prior = ledgers[name][-1] if ledgers[name] else None
            equity = Decimal(prior["equity"]) if prior else self.initial_cash
            month_start = Decimal(prior["month_start_equity"]) if prior else equity
            locked = bool(prior["month_locked"]) if prior else False
            previous_month = prior["day"][:7] if prior else None
            previous_outer = (
                Decimal(prior["state"].get("outer_exposure", "0")) if prior else Decimal("0")
            )
            for state in days:
                key = (name, state["day"])
                if key in existing:
                    continue
                month = state["day"][:7]
                if month != previous_month:
                    month_start = equity
                    locked = False
                    previous_month = month
                outer_exposure = Decimal("0") if locked else Decimal("4")
                raw_return = Decimal("0") if locked else _portfolio_return(state)
                outer_turnover_cost = (
                    abs(outer_exposure - previous_outer) * costs[2] / Decimal("10000")
                )
                daily_return = outer_exposure * raw_return - outer_turnover_cost
                equity *= Decimal("1") + daily_return
                month_return = equity / month_start - Decimal("1") if month_start else Decimal("-1")
                if month_return <= Decimal("-0.20") or month_return >= Decimal("0.18"):
                    locked = True
                state_payload = {
                    **state,
                    "ledger": name,
                    "raw_return": str(raw_return),
                    "outer_leverage": "4",
                    "outer_exposure": str(outer_exposure),
                    "outer_turnover_cost": str(outer_turnover_cost),
                    "month_return": str(month_return),
                    "month_locked": locked,
                    "implementation_version": IMPLEMENTATION_VERSION,
                    "metrics_state": state["metrics"]["state"],
                    "metrics_exposure": state["metrics"]["exposure"],
                    "immutable_forward_record": True,
                }
                state_payload["costs"] = {
                    **state["costs"],
                    "outer_route": str(outer_turnover_cost),
                }
                events = list(state["audit_events"])
                if outer_exposure != previous_outer:
                    events.append(
                        {
                            "timestamp_ms": state["timestamp_ms"] - DAY_MS + 1,
                            "sleeve_id": "outer_exposure",
                            "instrument_id": None,
                            "event_type": "OUTER_EXPOSURE_REBALANCE",
                            "target_before": str(previous_outer),
                            "target_after": str(outer_exposure),
                            "turnover": str(abs(outer_exposure - previous_outer)),
                            "route_cost": str(outer_turnover_cost),
                        }
                    )
                self.store.save_portfolio_day(
                    self.settings.id,
                    name,
                    state["day"],
                    state["timestamp_ms"],
                    equity,
                    daily_return,
                    month_start,
                    locked,
                    state_payload,
                    DATA_VERSION,
                    events,
                )
                previous_outer = outer_exposure
        self.last_update_ms = now_ms
        rows = self.store.portfolio_ledger(self.settings.id, "base")
        self.last_day = rows[-1]["day"] if rows else None
        self.status = "LIVE"
        self.status_message = (
            f"Forward ledger current through {self.last_day} UTC"
            if self.last_day
            else "Warm-up complete; waiting for first complete forward UTC day"
        )

    def view(self) -> dict[str, Any]:
        latest = self.store.portfolio_ledger(self.settings.id, "base")
        state = latest[-1]["state"] if latest else {}
        has_forward_day = bool(latest)
        month_locked = bool(state.get("month_locked"))
        decision_state = (
            "PAUSED"
            if month_locked
            else "PORTFOLIO_ACTIVE"
            if has_forward_day
            else "WAITING_FOR_DAILY_CLOSE"
        )
        return {
            "id": self.settings.id,
            "symbol": self.settings.symbol,
            "display_symbol": self.settings.display_symbol,
            "name": self.settings.name,
            "venue": self.settings.venue,
            "asset_type": "multi_asset_perpetual_portfolio",
            "reference_symbol": "BTC / ETH",
            "paper_model": "portfolio",
            "market_data_id": self.settings.primary_market_id,
            "allow_short": True,
            "leverage": 4,
            "margin_mode": "modeled_shared",
            "position_fraction": 1.0,
            "target_exposure": 4.0,
            "fee_bps": 5.0,
            "slippage_bps": 2.0,
            "strategy_family": "fixed_forward_calendar_router",
            "strategy_config": {
                "algorithm_version": self.definition["id"],
                "bar_minutes": 1440,
                "atr_period": 0,
                "atr_multiplier": 0,
                "trend_efficiency_period": 0,
                "minimum_trend_efficiency": 0,
                "reversal_confirmation_atr": 0,
                "one_action_per_bar": False,
                "startup_alignment": False,
                "futures_reversal_mode": "close_then_confirm",
                "signal_confirmation": "daily_close",
                "fill_timing": "next_daily_open",
            },
            "feed": "binance_futures_daily_rest",
            "market_state": state.get("metrics", {}),
            "kline_state": {
                "source": DATA_VERSION,
                "validation": self.input_validation,
                "details": self.input_validation_details or {},
                "last_official_bar_start_ms": state.get("timestamp_ms"),
                "last_verified_at_ms": self.last_update_ms,
                "mismatches": 0,
            },
            "status": self.status,
            "status_message": self.status_message,
            "strategy_ready": self.status == "LIVE",
            "reconnects": self.error_count,
            "last_tick": None,
            "strategy": {
                "ready": self.status == "LIVE",
                "atr": None,
                "trailing_stop": None,
                "price": None,
                "relation": (
                    "warming" if self.status != "LIVE" or not has_forward_day else "above"
                ),
                "bar_start_ms": state.get("timestamp_ms"),
                "bought_this_bar": False,
                "flattened_this_bar": False,
                "action_this_bar": False,
                "trend_efficiency": None,
                "trend_filter_passed": True,
                "reversal_direction": None,
                "reversal_anchor": None,
                "reversal_eligible_bar_ms": None,
                "last_cross": None,
                "last_cross_at_ms": None,
                "last_cross_result": None,
                "last_cross_reason": None,
            },
            "decision": {
                "state": decision_state,
                "reason": "UTC monthly lock" if month_locked else self.status_message,
                "next_trigger": "NEXT_UTC_DAILY_CLOSE",
                "trading_enabled": self.status == "LIVE",
                "has_position": has_forward_day and not month_locked,
                "position_side": "FLAT",
                "allow_short": True,
                "has_pending_order": False,
                "strategy_ready": self.status == "LIVE",
                "buy_lock_open": True,
                "reentry_lock_open": True,
                "action_lock_open": True,
                "trend_filter_passed": True,
                "reversal_direction": None,
                "reversal_eligible_bar_ms": None,
                "fresh_up_cross": False,
                "bar_end_ms": state.get("timestamp_ms"),
                "signal_confirmation": "DAILY_CLOSE",
                "fill_timing": "NEXT_DAILY_OPEN",
                "last_signal": None,
            },
        }


async def _klines_from(
    symbol: str,
    interval: str,
    interval_ms: int,
    start_ms: int,
) -> list[Bar]:
    """Load every complete bar from a fixed start, paging beyond Binance's 1,500 limit."""
    cursor = start_ms
    rows_by_start: dict[int, list[Any]] = {}
    now_ms = int(time.time() * 1000)
    async with httpx.AsyncClient(timeout=30, trust_env=True) as client:
        while cursor < now_ms:
            response = await client.get(
                f"{BINANCE_FUTURES_REST}/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "limit": 1500,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not payload:
                break
            for row in payload:
                rows_by_start[int(row[0])] = row
            next_cursor = int(payload[-1][0]) + interval_ms
            if next_cursor <= cursor:
                raise RuntimeError(f"{symbol} {interval} kline pagination did not advance")
            cursor = next_cursor
            if len(payload) < 1500:
                break
    return [
        Bar(
            int(row[0]),
            int(row[6]),
            *(Decimal(row[index]) for index in (1, 2, 3, 4, 5)),
            int(row[8]),
        )
        for _start, row in sorted(rows_by_start.items())
        if int(row[0]) >= start_ms and int(row[6]) < now_ms
    ]


async def _funding(symbol: str, start_ms: int, end_ms: int) -> list[FundingRate]:
    values: dict[int, FundingRate] = {}
    cursor = start_ms
    async with httpx.AsyncClient(timeout=30, trust_env=True) as client:
        while cursor <= end_ms:
            response = await client.get(
                f"{BINANCE_FUTURES_REST}/fundingRate",
                params={
                    "symbol": symbol,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not payload:
                break
            for row in payload:
                timestamp_ms = int(row["fundingTime"])
                values[timestamp_ms] = FundingRate(
                    timestamp_ms,
                    Decimal(row["fundingRate"]),
                    Decimal(row["markPrice"]),
                )
            next_cursor = int(payload[-1]["fundingTime"]) + 1
            if next_cursor <= cursor:
                raise RuntimeError(f"{symbol} funding pagination did not advance")
            cursor = next_cursor
            if len(payload) < 1000:
                break
    return [values[timestamp] for timestamp in sorted(values)]


async def _refresh_metric_history(store: PaperStore, symbol: str, now_ms: int) -> None:
    """Cache immutable archive history, then fill not-yet-archived buckets from REST."""
    end_day = datetime.fromtimestamp(now_ms / 1000, UTC).date() - timedelta(days=1)
    start_day = datetime.fromtimestamp(FOUR_HOUR_HISTORY_START_MS / 1000, UTC).date()
    existing = store.futures_metric_bars(
        symbol,
        240,
        int(datetime.combine(start_day, datetime.min.time(), UTC).timestamp() * 1000),
        now_ms,
    )
    day_counts: dict[str, int] = {}
    for item in existing:
        day = _day(item.start_ms)
        day_counts[day] = day_counts.get(day, 0) + 1
    loaded_days = {day for day, count in day_counts.items() if count >= DAY_MS // FOUR_HOUR_MS}
    missing_days = [
        start_day + timedelta(days=offset)
        for offset in range((end_day - start_day).days + 1)
        if (start_day + timedelta(days=offset)).isoformat() not in loaded_days
    ]
    if missing_days:
        semaphore = asyncio.Semaphore(12)
        async with httpx.AsyncClient(timeout=30, trust_env=True) as client:

            async def fetch(day: date) -> list[FuturesMetricBar]:
                async with semaphore:
                    return await _metric_archive(client, symbol, day)

            archived = await asyncio.gather(*(fetch(day) for day in missing_days))
        store.upsert_futures_metric_bars(
            symbol, 240, [bar for values in archived for bar in values]
        )
    store.upsert_futures_metric_bars(symbol, 240, await _metric_rest(symbol, now_ms))


async def _metric_archive(
    client: httpx.AsyncClient, symbol: str, day: date
) -> list[FuturesMetricBar]:
    name = f"{symbol}-metrics-{day.isoformat()}.zip"
    response = await client.get(f"{METRIC_ARCHIVE_BASE}/{symbol}/{name}")
    if response.status_code == 404:
        return []
    response.raise_for_status()
    groups: dict[int, tuple[int, Decimal, Decimal]] = {}
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        members = [item for item in bundle.namelist() if item.endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected Binance metric archive members for {day}")
        with bundle.open(members[0]) as source:
            for row in csv.DictReader(io.TextIOWrapper(source, encoding="utf-8")):
                try:
                    top = Decimal(row["sum_toptrader_long_short_ratio"])
                    global_account = Decimal(row["count_long_short_ratio"])
                    timestamp = int(
                        datetime.fromisoformat(row["create_time"]).replace(tzinfo=UTC).timestamp()
                        * 1000
                    )
                except (InvalidOperation, KeyError, TypeError, ValueError):
                    continue
                if top <= 0 or global_account <= 0:
                    continue
                bucket = timestamp // FOUR_HOUR_MS * FOUR_HOUR_MS
                previous = groups.get(bucket)
                if previous is None or timestamp > previous[0]:
                    groups[bucket] = (timestamp, top, global_account)
    return [
        FuturesMetricBar(
            start_ms,
            start_ms + FOUR_HOUR_MS - 1,
            values[1],
            values[2],
            "binance-vision-futures-metrics-5m",
        )
        for start_ms, values in sorted(groups.items())
    ]


async def _metric_rest(symbol: str, now_ms: int) -> list[FuturesMetricBar]:
    async with httpx.AsyncClient(timeout=30, trust_env=True) as client:
        top_response, global_response = await asyncio.gather(
            client.get(
                f"{METRIC_REST_BASE}/topLongShortPositionRatio",
                params={"symbol": symbol, "period": "4h", "limit": 500},
            ),
            client.get(
                f"{METRIC_REST_BASE}/globalLongShortAccountRatio",
                params={"symbol": symbol, "period": "4h", "limit": 500},
            ),
        )
    top_response.raise_for_status()
    global_response.raise_for_status()
    top = {int(row["timestamp"]): Decimal(row["longShortRatio"]) for row in top_response.json()}
    global_account = {
        int(row["timestamp"]): Decimal(row["longShortRatio"]) for row in global_response.json()
    }
    return [
        FuturesMetricBar(
            start_ms,
            start_ms + FOUR_HOUR_MS - 1,
            top[start_ms],
            global_account[start_ms],
            "binance-futures-metrics-rest-4h",
        )
        for start_ms in sorted(top.keys() & global_account.keys())
        if start_ms + FOUR_HOUR_MS - 1 < now_ms
        and top[start_ms] > 0
        and global_account[start_ms] > 0
    ]


def _market(instrument_id: str, symbol: str):
    from mastermind_tick.config import InstrumentSettings

    return InstrumentSettings(
        instrument_id,
        symbol,
        symbol,
        symbol,
        "crypto_perpetual",
        "Binance USD-M Futures",
        "USDT",
        "binance_futures",
        0.001,
        symbol,
    )


def _require_aligned(left: list[Bar], right: list[Bar]) -> None:
    if len(left) != len(right) or any(
        a.start_ms != b.start_ms for a, b in zip(left, right, strict=True)
    ):
        raise RuntimeError("BTC and ETH bars are not aligned")


def _validate_replay_inputs(
    btc4: list[Bar],
    eth4: list[Bar],
    btcd: list[Bar],
    ethd: list[Bar],
) -> dict[str, Any]:
    """Require causal signal history before allowing any forward ledger write."""
    _require_aligned(btc4, eth4)
    btc_daily_replay = [bar.start_ms for bar in btcd if bar.start_ms >= REPLAY_START_MS]
    eth_daily_replay = [bar.start_ms for bar in ethd if bar.start_ms >= REPLAY_START_MS]
    if btc_daily_replay != eth_daily_replay:
        raise RuntimeError("BTC and ETH daily replay bars are not aligned")
    details = {
        "btc_4h": _validate_bar_warmup(
            btc4,
            "BTCUSDT 4h",
            FOUR_HOUR_MS,
            FOUR_HOUR_HISTORY_START_MS,
            PRE_REPLAY_4H_BARS,
        ),
        "eth_4h": _validate_bar_warmup(
            eth4,
            "ETHUSDT 4h",
            FOUR_HOUR_MS,
            FOUR_HOUR_HISTORY_START_MS,
            PRE_REPLAY_4H_BARS,
        ),
        "btc_1d": _validate_bar_warmup(
            btcd,
            "BTCUSDT 1d",
            DAY_MS,
            None,
            DAILY_WARMUP_BARS,
        ),
        "eth_1d": _validate_bar_warmup(
            ethd,
            "ETHUSDT 1d",
            DAY_MS,
            None,
            DAILY_WARMUP_BARS,
        ),
    }
    for label, bars in (("BTCUSDT", btc4), ("ETHUSDT", eth4)):
        replay_index = bisect.bisect_left([bar.start_ms for bar in bars], REPLAY_START_MS)
        if _zreturns(bars, SHOCK_WINDOW_60D)[replay_index - 1] is None:
            raise RuntimeError(f"{label} 60-day shock signal is unavailable before replay start")
    longest_macd = "btc_perp-macd-1440m-16-48-14-long_only-confirm3"
    for label, bars in (("BTCUSDT", btcd), ("ETHUSDT", ethd)):
        replay_index = bisect.bisect_left([bar.start_ms for bar in bars], REPLAY_START_MS)
        if _macd_candidate(bars, longest_macd)[replay_index - 1] is None:
            raise RuntimeError(f"{label} longest MACD signal is unavailable before replay start")
    return details


def _validate_bar_warmup(
    bars: list[Bar],
    label: str,
    interval_ms: int,
    required_start_ms: int | None,
    required_pre_replay_bars: int,
) -> dict[str, Any]:
    if not bars:
        raise RuntimeError(f"{label} history is empty")
    if required_start_ms is not None and bars[0].start_ms > required_start_ms:
        raise RuntimeError(
            f"{label} history starts at {bars[0].start_ms}, after required {required_start_ms}"
        )
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.start_ms != previous.start_ms + interval_ms:
            raise RuntimeError(
                f"{label} history gap between {previous.start_ms} and {current.start_ms}"
            )
    starts = [bar.start_ms for bar in bars]
    replay_index = bisect.bisect_left(starts, REPLAY_START_MS)
    if replay_index >= len(bars) or starts[replay_index] != REPLAY_START_MS:
        raise RuntimeError(f"{label} does not contain the replay-start bar")
    if replay_index < required_pre_replay_bars:
        raise RuntimeError(
            f"{label} has {replay_index} pre-replay bars; requires {required_pre_replay_bars}"
        )
    return {
        "start_ms": bars[0].start_ms,
        "end_ms": bars[-1].end_ms,
        "bar_count": len(bars),
        "pre_replay_bar_count": replay_index,
        "continuous": True,
    }


def _validate_metric_warmup(eth4: list[Bar], metrics: list[FuturesMetricBar]) -> dict[str, Any]:
    score_at = REPLAY_START_MS - FOUR_HOUR_MS
    scores = _metric_zscores(eth4, metrics)
    score, source = scores.get(score_at, (None, None))
    if score is None:
        raise RuntimeError("ETH futures metric z-score is unavailable before replay start")
    return {
        "start_ms": metrics[0].start_ms,
        "end_ms": metrics[-1].end_ms,
        "bar_count": len(metrics),
        "first_replay_score_at_ms": score_at,
        "first_replay_score": str(score),
        "source": source,
    }


def _daily_states(
    definition: dict[str, Any],
    btc4: list[Bar],
    eth4: list[Bar],
    btcd: list[Bar],
    ethd: list[Bar],
    btc_funding: list[FundingRate],
    eth_funding: list[FundingRate],
    eth_metrics: list[FuturesMetricBar],
    fee_bps: Decimal,
    slippage_bps: Decimal,
    route_cost_bps: Decimal,
) -> list[dict[str, Any]]:
    btc_scores_15, eth_scores_15 = _shock_scores(btc4, eth4, 90)
    btc_scores_60, eth_scores_60 = _shock_scores(btc4, eth4, 360)
    lead = _weighted(
        _shock_targets(btc_scores_15, eth_scores_15, Decimal("2"), 12, "underreaction"),
        btc_scores_15,
    )
    eth_eth = _event(
        _shock_targets(eth_scores_60, eth_scores_60, Decimal("2.5"), 12, "none"),
        True,
    )
    btc_btc = _event(
        _shock_targets(btc_scores_15, btc_scores_15, Decimal("2"), 4, "none"),
        False,
    )
    eth_btc = _event(
        _shock_targets(
            eth_scores_60,
            btc_scores_60,
            Decimal("1.5"),
            12,
            "underreaction",
        ),
        False,
    )
    mapping = {
        int(month): tuple(candidates)
        for month, candidates in definition["parameters"]["fixed_month_mapping"].items()
    }
    if mapping != _mapping():
        raise RuntimeError("strategy JSON does not match the frozen runtime mapping")
    daily_targets = {
        candidate: _macd_candidate(btcd if candidate.startswith("btc") else ethd, candidate)
        for candidate in _all_macd_candidates()
    }
    replay_end_ms = min(btc4[-1].end_ms, eth4[-1].end_ms)
    component_specs = {
        "lead_lag": (eth4, lead, eth_funding, Decimal("0.15"), "eth_perp"),
        ("event-eth_perp-to-eth_perp-continuation-60d-threshold-2p5-hold-12x4h-none-long_only"): (
            eth4,
            eth_eth,
            eth_funding,
            None,
            "eth_perp",
        ),
        ("event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short"): (
            btc4,
            btc_btc,
            btc_funding,
            None,
            "btc_perp",
        ),
        (
            "event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-"
            "hold-12x4h-underreaction-long_short"
        ): (btc4, eth_btc, btc_funding, None, "btc_perp"),
    }
    allocations = {
        "lead_lag": Decimal("0.40"),
        (
            "event-eth_perp-to-eth_perp-continuation-60d-threshold-2p5-hold-12x4h-none-long_only"
        ): Decimal("0.15"),
        (
            "event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short"
        ): Decimal("0.30"),
        (
            "event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-"
            "hold-12x4h-underreaction-long_short"
        ): Decimal("0.15"),
    }
    component_replays = {
        sleeve_id: replay_independent_sleeve(
            bars,
            targets,
            funding,
            start_ms=REPLAY_START_MS,
            end_ms=replay_end_ms,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            monthly_loss_limit=monthly_loss_limit,
        )
        for sleeve_id, (
            bars,
            targets,
            funding,
            monthly_loss_limit,
            _instrument_id,
        ) in component_specs.items()
    }
    anchor = combine_static_anchor(component_replays, allocations, leverage=Decimal("4"))
    metric_scores = _metric_zscores(eth4, eth_metrics)
    metrics_by_day = {}
    for day in anchor:
        day_start_ms = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
        metric_start_ms = day_start_ms - FOUR_HOUR_MS
        score, source = metric_scores.get(metric_start_ms, (None, None))
        metrics_by_day[day] = (score, source, metric_start_ms)
    state = apply_state_volatility_overlay(
        anchor,
        metrics_by_day,
        route_cost_bps=route_cost_bps,
    )
    trend_replays = {
        candidate: replay_independent_sleeve(
            btcd if candidate.startswith("btc") else ethd,
            tuple(Decimal(value) if value is not None else None for value in targets),
            btc_funding if candidate.startswith("btc") else eth_funding,
            start_ms=REPLAY_START_MS,
            end_ms=min(btcd[-1].end_ms, ethd[-1].end_ms),
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        for candidate, targets in daily_targets.items()
    }
    route = combine_calendar_route(
        state,
        trend_replays,
        mapping,
        route_cost_bps=route_cost_bps,
    )
    result = []
    for day, route_day in route.items():
        day_start_ms = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
        if day_start_ms < FORWARD_START_MS:
            continue
        selected = route_day["selected"]
        state_day = state[day]
        anchor_day = anchor[day]
        audit_events: list[dict[str, Any]] = []
        component_details = {}
        state_base_targets = {"btc": Decimal("0"), "eth": Decimal("0")}
        for sleeve_id, (
            _bars,
            _targets,
            _funding,
            _monthly_loss_limit,
            instrument_id,
        ) in component_specs.items():
            sleeve_day = component_replays[sleeve_id][day]
            allocation = allocations[sleeve_id]
            asset = "btc" if instrument_id == "btc_perp" else "eth"
            state_base_targets[asset] += Decimal("4") * allocation * sleeve_day.target
            events = attach_events(
                sleeve_day,
                sleeve_id,
                instrument_id,
                capitalized=True,
            )
            for event in events:
                event["anchor_allocation"] = str(allocation)
                event["anchor_leverage"] = "4"
            audit_events.extend(events)
            component_details[sleeve_id] = {
                "instrument_id": instrument_id,
                "allocation": str(allocation),
                "cash": str(sleeve_day.cash),
                "quantity": str(sleeve_day.quantity),
                "equity": str(sleeve_day.equity),
                "target": str(sleeve_day.target),
                "return": str(sleeve_day.daily_return),
                "fee_amount": str(sleeve_day.fee_amount),
                "slippage_amount": str(sleeve_day.slippage_amount),
                "funding_amount": str(sleeve_day.funding_amount),
                "allocated_equity": str(anchor_day["allocated_equity"][sleeve_id]),
            }
        trend_details = {}
        for candidate in selected:
            sleeve_day = trend_replays[candidate][day]
            instrument_id = "btc_perp" if candidate.startswith("btc") else "eth_perp"
            audit_events.extend(
                attach_events(
                    sleeve_day,
                    candidate,
                    instrument_id,
                    capitalized=True,
                )
            )
            trend_details[candidate] = {
                "instrument_id": instrument_id,
                "cash": str(sleeve_day.cash),
                "quantity": str(sleeve_day.quantity),
                "equity": str(sleeve_day.equity),
                "target": str(sleeve_day.target),
                "return": str(sleeve_day.daily_return),
                "fee_amount": str(sleeve_day.fee_amount),
                "slippage_amount": str(sleeve_day.slippage_amount),
                "funding_amount": str(sleeve_day.funding_amount),
            }
        if state_day["route_turnover"]:
            audit_events.append(
                {
                    "timestamp_ms": day_start_ms,
                    "sleeve_id": "state_overlay",
                    "instrument_id": None,
                    "event_type": "STATE_EXPOSURE_REBALANCE",
                    "target_before": None,
                    "target_after": str(state_day["combined_exposure"]),
                    "metric_exposure": str(state_day["signal_exposure"]),
                    "volatility_exposure": str(state_day["volatility_exposure"]),
                    "turnover": str(state_day["route_turnover"]),
                    "route_cost": str(state_day["route_cost"]),
                    "capitalized": True,
                }
            )
        if route_day["route_turnover"]:
            audit_events.append(
                {
                    "timestamp_ms": day_start_ms,
                    "sleeve_id": "calendar_route",
                    "instrument_id": None,
                    "event_type": "CALENDAR_ROUTE_REBALANCE",
                    "weights_before": {
                        key: str(value) for key, value in route_day["route_weights_before"].items()
                    },
                    "weights_after": {
                        key: str(value) for key, value in route_day["route_weights"].items()
                    },
                    "turnover": str(route_day["route_turnover"]),
                    "route_cost": str(route_day["route_cost"]),
                    "capitalized": True,
                }
            )
        metric_score = state_day["metric_score"]
        metric_exposure = state_day["signal_exposure"]
        combined_exposure = state_day["combined_exposure"]
        metrics_state = (
            "unavailable"
            if metric_score is None
            else "high"
            if metric_exposure == Decimal("2")
            else "normal"
        )
        result.append(
            {
                "day": day,
                "timestamp_ms": day_start_ms + DAY_MS - 1,
                "raw_return": str(route_day["return"]),
                "state_return": str(state_day["return"]),
                "state_anchor_return": str(anchor_day["return"]),
                "state_signal_return_for_volatility": str(state_day["signal_return"]),
                "state_targets": {
                    key: str(value * combined_exposure) for key, value in state_base_targets.items()
                },
                "state_base_targets": {
                    key: str(value) for key, value in state_base_targets.items()
                },
                "state_metric_exposure": str(metric_exposure),
                "state_volatility_exposure": str(state_day["volatility_exposure"]),
                "state_combined_exposure": str(combined_exposure),
                "state_realized_rms": (
                    str(state_day["rms"]) if state_day["rms"] is not None else None
                ),
                "trend_targets": {
                    candidate: str(trend_replays[candidate][day].target) for candidate in selected
                },
                "trend_selected": list(selected),
                "shadow_candidate_count": len(trend_replays),
                "metrics": {
                    "state": metrics_state,
                    "zscore": str(metric_score) if metric_score is not None else None,
                    "exposure": str(metric_exposure),
                    "bar_start_ms": state_day["metric_start_ms"],
                    "source": state_day["metric_source"],
                    "window_bars": METRIC_WINDOW,
                },
                "costs": {
                    "component_fee": str(-route_day["fee_return"]),
                    "component_slippage": str(-route_day["slippage_return"]),
                    "state_route": str(Decimal("0.5") * state_day["route_cost"]),
                    "calendar_route": str(route_day["route_cost"]),
                    "outer_route": "0",
                },
                "funding_return": str(route_day["funding_return"]),
                "audit_events": audit_events,
                "sleeves": {
                    "state": {
                        "return": str(state_day["return"]),
                        "anchor_equity": str(anchor_day["equity"]),
                        "borrow_reserve": str(anchor_day["reserve"]),
                        "signal_exposure": str(metric_exposure),
                        "volatility_exposure": str(state_day["volatility_exposure"]),
                        "combined_exposure": str(combined_exposure),
                        "components": component_details,
                    },
                    "trend": {
                        "selected": list(selected),
                        "route_turnover": str(route_day["route_turnover"]),
                        "route_cost": str(route_day["route_cost"]),
                        "components": trend_details,
                    },
                },
            }
        )
    return result


def _portfolio_return(state: dict[str, Any]) -> Decimal:
    """Return an already costed raw portfolio day; outer exposure is applied separately."""
    return Decimal(state["raw_return"])


def _causal_volatility_exposure(
    prior_state_returns: deque[Decimal],
) -> tuple[Decimal, Decimal | None]:
    """Use exactly 20 returns that closed before the exposure day."""
    if len(prior_state_returns) < 20:
        return Decimal("1"), None
    rms = (
        sum((value * value for value in prior_state_returns), Decimal("0"))
        / Decimal(len(prior_state_returns))
    ).sqrt()
    exposure = (
        Decimal("1.1")
        if rms == 0
        else min(Decimal("1.1"), max(Decimal("0.6"), Decimal("0.03") / rms))
    )
    return exposure, rms


def _route_turnover(
    previous_weights: dict[str, Decimal], current_weights: dict[str, Decimal]
) -> Decimal:
    names = set(previous_weights) | set(current_weights)
    return sum(
        (
            abs(current_weights.get(name, Decimal("0")) - previous_weights.get(name, Decimal("0")))
            for name in names
        ),
        Decimal("0"),
    ) / Decimal("2")


def _all_macd_candidates() -> tuple[str, ...]:
    return tuple(
        f"{asset}_perp-macd-1440m-{fast}-{slow}-{signal}-long_only-confirm{confirmation}"
        for asset in ("btc", "eth")
        for fast, slow in ((5, 15), (8, 24), (10, 30), (12, 36), (16, 48))
        for signal in (5, 9, 14)
        for confirmation in (1, 2, 3)
    )


def _funding_by_bar(bars: list[Bar], values: list[FundingRate]) -> list[list[FundingRate]]:
    ends = [bar.end_ms for bar in bars]
    result: list[list[FundingRate]] = [[] for _bar in bars]
    for value in values:
        index = bisect.bisect_left(ends, value.timestamp_ms)
        if index < len(bars) and bars[index].start_ms <= value.timestamp_ms:
            result[index].append(value)
    return result


def _metric_zscores(
    bars: list[Bar], metrics: list[FuturesMetricBar]
) -> dict[int, tuple[Decimal | None, str | None]]:
    by_start = {item.start_ms: item for item in metrics}
    values: deque[Decimal | None] = deque()
    total = Decimal("0")
    total_squared = Decimal("0")
    missing = 0
    result: dict[int, tuple[Decimal | None, str | None]] = {}
    for bar in bars:
        metric = by_start.get(bar.start_ms)
        value = (
            (metric.top_position_ratio / metric.global_account_ratio).ln()
            if metric is not None
            and metric.top_position_ratio > 0
            and metric.global_account_ratio > 0
            else None
        )
        values.append(value)
        if value is None:
            missing += 1
        else:
            total += value
            total_squared += value * value
        if len(values) > METRIC_WINDOW:
            expired = values.popleft()
            if expired is None:
                missing -= 1
            else:
                total -= expired
                total_squared -= expired * expired
        score = None
        if len(values) == METRIC_WINDOW and not missing and value is not None:
            mean = total / Decimal(METRIC_WINDOW)
            variance = max(Decimal("0"), total_squared / Decimal(METRIC_WINDOW) - mean * mean)
            deviation = variance.sqrt()
            score = (
                Decimal("0") if deviation <= Decimal("0.00000001") else (value - mean) / deviation
            )
            score = max(Decimal("-8"), min(Decimal("8"), score))
        result[bar.start_ms] = (score, metric.source if metric is not None else None)
    return result


def _macd_candidate(bars: list[Bar], candidate: str) -> tuple[int | None, ...]:
    parts = candidate.split("-")
    fast, slow, signal = (int(value) for value in parts[3:6])
    confirmation = int(parts[-1].replace("confirm", ""))
    fa, sa, siga = (Decimal("2") / Decimal(value + 1) for value in (fast, slow, signal))
    fv = sv = sigv = None
    raw = []
    for index, bar in enumerate(bars):
        fv = bar.close if fv is None else fv + fa * (bar.close - fv)
        sv = bar.close if sv is None else sv + sa * (bar.close - sv)
        macd = fv - sv
        sigv = macd if sigv is None else sigv + siga * (macd - sigv)
        raw.append(None if index + 1 < slow + signal - 1 else int(macd > sigv))
    count = 0
    result = []
    for value in raw:
        if value is None:
            result.append(None)
        elif value == 0:
            count = 0
            result.append(0)
        else:
            count += 1
            result.append(1 if count >= confirmation else 0)
    return tuple(result)


def _shock_scores(btc: list[Bar], eth: list[Bar], window: int):
    return _zreturns(btc, window), _zreturns(eth, window)


def _zreturns(bars: list[Bar], window: int) -> tuple[Decimal | None, ...]:
    values = [None] + [
        bars[i].close / bars[i - 1].close - Decimal("1") for i in range(1, len(bars))
    ]
    result = []
    for index, value in enumerate(values):
        history = [v for v in values[max(0, index - window) : index] if v is not None]
        if value is None or len(history) < window:
            result.append(None)
            continue
        mean = sum(history, Decimal("0")) / Decimal(window)
        variance = sum(((v - mean) ** 2 for v in history), Decimal("0")) / Decimal(window)
        result.append((value - mean) / variance.sqrt() if variance else Decimal("0"))
    return tuple(result)


def _shock_targets(source, traded, threshold: Decimal, hold: int, gate: str) -> tuple[int, ...]:
    state, exit_index = 0, -1
    result = []
    for index, (left, right) in enumerate(zip(source, traded, strict=True)):
        if state and index >= exit_index:
            state = 0
        elif not state and left is not None and right is not None:
            side = 1 if left >= threshold else -1 if left <= -threshold else 0
            passed = gate == "none" or (
                gate == "underreaction" and abs(right) <= abs(left) * Decimal("0.75")
            )
            if side and passed:
                state, exit_index = side, index + hold
        result.append(state)
    return tuple(result)


def _weighted(targets, scores) -> tuple[Decimal, ...]:
    active = Decimal("0")
    result = []
    for target, score in zip(targets, scores, strict=True):
        if not target:
            active = Decimal("0")
        elif not active and score is not None:
            magnitude = abs(score)
            exposure = (
                Decimal("2")
                if magnitude >= 3
                else Decimal("1.5")
                if magnitude >= Decimal("2.5")
                else Decimal("0.5")
            )
            active = exposure if target > 0 else -exposure
        result.append(active)
    return tuple(result)


def _event(targets, long_only: bool) -> tuple[int, ...]:
    return tuple(max(0, value) if long_only else value for value in targets)


def _day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).date().isoformat()


def _funding_by_day(values: list[FundingRate]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for value in values:
        day = _day(value.timestamp_ms)
        result[day] = result.get(day, Decimal("0")) + value.rate
    return result


def _mapping() -> dict[int, tuple[str, ...]]:
    # Kept explicit so runtime behavior does not depend on mutable research output.
    return {
        1: (
            "btc_perp-macd-1440m-8-24-5-long_only-confirm1",
            "btc_perp-macd-1440m-5-15-9-long_only-confirm1",
            "btc_perp-macd-1440m-8-24-5-long_only-confirm2",
        ),
        2: (
            "btc_perp-macd-1440m-12-36-14-long_only-confirm1",
            "btc_perp-macd-1440m-10-30-14-long_only-confirm2",
            "btc_perp-macd-1440m-10-30-14-long_only-confirm1",
        ),
        3: (
            "btc_perp-macd-1440m-12-36-14-long_only-confirm1",
            "eth_perp-macd-1440m-5-15-9-long_only-confirm2",
            "btc_perp-macd-1440m-16-48-9-long_only-confirm1",
        ),
        4: (
            "btc_perp-macd-1440m-12-36-5-long_only-confirm3",
            "btc_perp-macd-1440m-10-30-5-long_only-confirm3",
            "btc_perp-macd-1440m-8-24-9-long_only-confirm3",
        ),
        5: (
            "eth_perp-macd-1440m-12-36-14-long_only-confirm3",
            "eth_perp-macd-1440m-10-30-9-long_only-confirm2",
            "eth_perp-macd-1440m-12-36-5-long_only-confirm2",
        ),
        6: (
            "btc_perp-macd-1440m-5-15-5-long_only-confirm1",
            "btc_perp-macd-1440m-16-48-14-long_only-confirm1",
            "btc_perp-macd-1440m-5-15-9-long_only-confirm3",
        ),
        7: (
            "eth_perp-macd-1440m-5-15-9-long_only-confirm2",
            "eth_perp-macd-1440m-8-24-9-long_only-confirm3",
            "eth_perp-macd-1440m-10-30-5-long_only-confirm3",
        ),
        8: (
            "eth_perp-macd-1440m-12-36-5-long_only-confirm3",
            "btc_perp-macd-1440m-5-15-9-long_only-confirm3",
            "eth_perp-macd-1440m-10-30-9-long_only-confirm3",
        ),
        9: (
            "btc_perp-macd-1440m-5-15-14-long_only-confirm2",
            "btc_perp-macd-1440m-12-36-5-long_only-confirm1",
            "btc_perp-macd-1440m-8-24-14-long_only-confirm1",
        ),
        10: (
            "btc_perp-macd-1440m-5-15-5-long_only-confirm1",
            "btc_perp-macd-1440m-5-15-5-long_only-confirm2",
            "btc_perp-macd-1440m-5-15-5-long_only-confirm3",
        ),
        11: (
            "btc_perp-macd-1440m-16-48-14-long_only-confirm1",
            "btc_perp-macd-1440m-16-48-14-long_only-confirm3",
            "btc_perp-macd-1440m-16-48-14-long_only-confirm2",
        ),
        12: (
            "eth_perp-macd-1440m-16-48-14-long_only-confirm3",
            "eth_perp-macd-1440m-16-48-14-long_only-confirm1",
            "eth_perp-macd-1440m-16-48-14-long_only-confirm2",
        ),
    }
