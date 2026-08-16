"""Forward-only paper ledger for the frozen BTC/ETH calendar router."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from mastermind_tick.config import PortfolioPaperSettings
from mastermind_tick.feeds import BINANCE_FUTURES_REST
from mastermind_tick.models import Bar, FundingRate
from mastermind_tick.store import PaperStore

DAY_MS = 86_400_000
FOUR_HOUR_MS = 14_400_000
DATA_VERSION = "binance-public-closed-bars-v1"


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
            _klines("BTCUSDT", "4h", 600),
            _klines("ETHUSDT", "4h", 600),
            _klines("BTCUSDT", "1d", 120),
            _klines("ETHUSDT", "1d", 120),
            _funding("BTCUSDT", now_ms - 100 * DAY_MS, now_ms),
            _funding("ETHUSDT", now_ms - 100 * DAY_MS, now_ms),
        )
        if len(btc_4h) < 540 or len(eth_4h) < 540:
            raise RuntimeError("fewer than 540 complete 4h warm-up bars")
        if len(btc_daily) < 80 or len(eth_daily) < 80:
            raise RuntimeError("fewer than 80 complete daily warm-up bars")
        _require_aligned(btc_4h, eth_4h)
        _require_aligned(btc_daily, eth_daily)
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

        days = _daily_states(
            self.definition,
            btc_4h,
            eth_4h,
            btc_daily,
            eth_daily,
            btc_funding,
            eth_funding,
        )
        existing = {
            (row["ledger"], row["day"])
            for ledger in ("base", "stress")
            for row in self.store.portfolio_ledger(self.settings.id, ledger)
        }
        ledgers = {
            name: self.store.portfolio_ledger(self.settings.id, name) for name in ("base", "stress")
        }
        for name, costs in (
            ("base", (Decimal("7"), Decimal("7"))),
            ("stress", (Decimal("15"), Decimal("15"))),
        ):
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
                raw_return = Decimal("0") if locked else _portfolio_return(state, *costs)
                outer_turnover_cost = (
                    abs(outer_exposure - previous_outer) * costs[1] / Decimal("10000")
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
                    "metrics_state": "unavailable",
                    "metrics_exposure": "1",
                    "sleeve_costs": {
                        sleeve_id: str(
                            Decimal(sleeve["turnover"]) * (costs[0] + costs[1]) / Decimal("10000")
                        )
                        for sleeve_id, sleeve in state["sleeves"].items()
                    },
                    "immutable_forward_record": True,
                }
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
            "market_state": {},
            "kline_state": {
                "source": DATA_VERSION,
                "validation": "COMPLETE" if self.status == "LIVE" else "PENDING",
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
                "relation": "warming" if self.status != "LIVE" else "above",
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
                "state": "PAUSED" if state.get("month_locked") else "HOLDING_LONG",
                "reason": "UTC monthly lock" if state.get("month_locked") else self.status_message,
                "next_trigger": "Next complete UTC daily bar",
                "trading_enabled": self.status == "LIVE",
                "has_position": bool(state),
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


async def _klines(symbol: str, interval: str, limit: int) -> list[Bar]:
    async with httpx.AsyncClient(timeout=30, trust_env=True) as client:
        response = await client.get(
            f"{BINANCE_FUTURES_REST}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        response.raise_for_status()
        payload = response.json()
    now_ms = int(time.time() * 1000)
    return [
        Bar(
            int(row[0]),
            int(row[6]),
            *(Decimal(row[index]) for index in (1, 2, 3, 4, 5)),
            int(row[8]),
        )
        for row in payload
        if int(row[6]) < now_ms
    ]


async def _funding(symbol: str, start_ms: int, end_ms: int) -> list[FundingRate]:
    async with httpx.AsyncClient(timeout=30, trust_env=True) as client:
        response = await client.get(
            f"{BINANCE_FUTURES_REST}/fundingRate",
            params={"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
        )
        response.raise_for_status()
        payload = response.json()
    return [
        FundingRate(int(row["fundingTime"]), Decimal(row["fundingRate"]), Decimal(row["markPrice"]))
        for row in payload
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


def _daily_states(
    definition: dict[str, Any],
    btc4: list[Bar],
    eth4: list[Bar],
    btcd: list[Bar],
    ethd: list[Bar],
    btc_funding: list[FundingRate],
    eth_funding: list[FundingRate],
) -> list[dict[str, Any]]:
    btc_scores_15, eth_scores_15 = _shock_scores(btc4, eth4, 90)
    btc_scores_60, eth_scores_60 = _shock_scores(btc4, eth4, 360)
    lead = _weighted(
        _shock_targets(btc_scores_15, eth_scores_15, Decimal("2"), 12, "underreaction"),
        btc_scores_15,
    )
    eth_eth = _event(_shock_targets(eth_scores_60, eth_scores_60, Decimal("2.5"), 12, "none"), True)
    btc_btc = _event(_shock_targets(btc_scores_15, btc_scores_15, Decimal("2"), 4, "none"), False)
    eth_btc = _event(
        _shock_targets(eth_scores_60, btc_scores_60, Decimal("1.5"), 12, "underreaction"), False
    )
    mapping = {
        int(month): tuple(candidates)
        for month, candidates in definition["parameters"]["fixed_month_mapping"].items()
    }
    if mapping != _mapping():
        raise RuntimeError("strategy JSON does not match the frozen runtime mapping")
    daily_targets = {
        candidate: _macd_candidate(btcd if candidate.startswith("btc") else ethd, candidate)
        for candidates in mapping.values()
        for candidate in candidates
    }
    by_day_4h: dict[str, list[int]] = {}
    for index, bar in enumerate(btc4):
        by_day_4h.setdefault(_day(bar.start_ms), []).append(index)
    funding_by_day = {
        "btc": _funding_by_day(btc_funding),
        "eth": _funding_by_day(eth_funding),
    }
    start = datetime(2026, 8, 16, tzinfo=UTC).timestamp() * 1000
    result = []
    state_returns: deque[Decimal] = deque(maxlen=20)
    previous_state = {"btc": Decimal("0"), "eth": Decimal("0")}
    previous_trend: dict[str, Decimal] = {}
    for day_index, (btc, eth) in enumerate(zip(btcd, ethd, strict=True)):
        day = _day(btc.start_ms)
        indices = by_day_4h.get(day, [])
        state_return = Decimal("0")
        turnover = Decimal("0")
        state_turnover = Decimal("0")
        end_targets = dict(previous_state)
        for index in indices:
            btc_target = Decimal("4") * (
                Decimal("0.30") * Decimal(btc_btc[index])
                + Decimal("0.15") * Decimal(eth_btc[index])
            )
            eth_target = Decimal("4") * (
                Decimal("0.40") * Decimal(lead[index]) + Decimal("0.15") * Decimal(eth_eth[index])
            )
            if index:
                btc_ret = btc4[index].close / btc4[index].open - Decimal("1")
                eth_ret = eth4[index].close / eth4[index].open - Decimal("1")
                state_return += previous_state["btc"] * btc_ret + previous_state["eth"] * eth_ret
            target_turnover = abs(btc_target - previous_state["btc"]) + abs(
                eth_target - previous_state["eth"]
            )
            state_turnover += target_turnover
            turnover += target_turnover
            previous_state = {"btc": btc_target, "eth": eth_target}
            end_targets = dict(previous_state)
        state_return -= end_targets["btc"] * funding_by_day["btc"].get(
            day, Decimal("0")
        ) + end_targets["eth"] * funding_by_day["eth"].get(day, Decimal("0"))
        vol = (
            (
                sum((value * value for value in state_returns), Decimal("0"))
                / Decimal(len(state_returns))
            ).sqrt()
            if state_returns
            else Decimal("0")
        )
        vol_exposure = (
            min(Decimal("1.1"), max(Decimal("0.6"), Decimal("0.03") / vol)) if vol else Decimal("1")
        )
        state_return *= vol_exposure
        state_returns.append(state_return)
        selected = mapping[datetime.fromtimestamp(btc.start_ms / 1000, UTC).month]
        trend_returns = []
        trend_targets = {}
        trend_turnovers = {}
        for candidate in selected:
            target = (
                Decimal(daily_targets[candidate][day_index - 1] or 0) if day_index else Decimal("0")
            )
            asset_bar = btc if candidate.startswith("btc") else eth
            asset_return = asset_bar.close / asset_bar.open - Decimal("1")
            trend_returns.append(target * asset_return)
            target_turnover = abs(target - previous_trend.get(candidate, Decimal("0")))
            turnover += target_turnover
            trend_turnovers[candidate] = target_turnover
            trend_targets[candidate] = str(target)
            previous_trend[candidate] = target
        for candidate, target_value in trend_targets.items():
            asset = "btc" if candidate.startswith("btc") else "eth"
            funding_cost = Decimal(target_value) * funding_by_day[asset].get(day, Decimal("0"))
            trend_returns[selected.index(candidate)] -= funding_cost
        if btc.start_ms >= start:
            result.append(
                {
                    "day": day,
                    "timestamp_ms": btc.end_ms,
                    "state_return": str(state_return),
                    "state_targets": {k: str(v) for k, v in end_targets.items()},
                    "state_volatility_exposure": str(vol_exposure),
                    "trend_returns": [str(v) for v in trend_returns],
                    "trend_targets": trend_targets,
                    "turnover": str(turnover),
                    "sleeves": {
                        "state": {
                            "targets": {key: str(value) for key, value in end_targets.items()},
                            "return": str(state_return),
                            "turnover": str(state_turnover),
                        },
                        **{
                            candidate: {
                                "asset": "btc_perp" if candidate.startswith("btc") else "eth_perp",
                                "target": trend_targets[candidate],
                                "return": str(trend_returns[index]),
                                "turnover": str(trend_turnovers[candidate]),
                            }
                            for index, candidate in enumerate(selected)
                        },
                    },
                }
            )
    return result


def _portfolio_return(
    state: dict[str, Any], component_cost_bps: Decimal, route_cost_bps: Decimal
) -> Decimal:
    gross = Decimal("0.5") * Decimal(state["state_return"]) + sum(
        (Decimal(v) for v in state["trend_returns"]), Decimal("0")
    ) / Decimal("6")
    return gross - Decimal(state["turnover"]) * (component_cost_bps + route_cost_bps) / Decimal(
        "10000"
    )


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
