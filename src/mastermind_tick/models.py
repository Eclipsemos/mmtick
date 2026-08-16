"""Small domain objects shared by feeds, strategy, and broker."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Tick:
    event_id: str
    timestamp_ms: int
    price: Decimal
    quantity: Decimal
    source: str
    aggregate_trade_id: int | None = None
    first_trade_id: int | None = None
    last_trade_id: int | None = None
    buyer_is_maker: bool | None = None
    event_time_ms: int | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_time_ms: int | None = None
    open_price: Decimal | None = None
    high_price: Decimal | None = None
    low_price: Decimal | None = None
    notional: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["price"] = str(self.price)
        value["quantity"] = str(self.quantity)
        for key in (
            "mark_price",
            "index_price",
            "funding_rate",
            "open_price",
            "high_price",
            "low_price",
            "notional",
        ):
            if value[key] is not None:
                value[key] = str(value[key])
        return value


@dataclass(frozen=True)
class FundingRate:
    timestamp_ms: int
    rate: Decimal
    mark_price: Decimal


@dataclass(frozen=True)
class FuturesMetricBar:
    """Closed Binance futures positioning snapshot reduced to one 4h bucket."""

    start_ms: int
    end_ms: int
    top_position_ratio: Decimal
    global_account_ratio: Decimal
    source: str


@dataclass
class Bar:
    start_ms: int
    end_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")
    trade_count: int = 0

    def update(self, tick: Tick) -> None:
        self.high = max(self.high, tick.high_price or tick.price)
        self.low = min(self.low, tick.low_price or tick.price)
        self.close = tick.price
        self.volume += tick.quantity
        self.trade_count += (
            tick.last_trade_id - tick.first_trade_id + 1
            if tick.first_trade_id is not None and tick.last_trade_id is not None
            else 1
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "trade_count": self.trade_count,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Bar:
        return cls(
            start_ms=int(value["start_ms"]),
            end_ms=int(value["end_ms"]),
            open=Decimal(str(value["open"])),
            high=Decimal(str(value["high"])),
            low=Decimal(str(value["low"])),
            close=Decimal(str(value["close"])),
            volume=Decimal(str(value.get("volume", "0"))),
            trade_count=int(value.get("trade_count", 0)),
        )


@dataclass(frozen=True)
class StrategySignal:
    side: Side
    reason: str
    signal_price: Decimal
    trailing_stop: Decimal
    atr: Decimal
    bar_start_ms: int
    tick_id: str
    signal_at_ms: int | None = None
    reduce_only: bool = False
