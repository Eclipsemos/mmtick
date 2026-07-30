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

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["price"] = str(self.price)
        value["quantity"] = str(self.quantity)
        return value


@dataclass
class Bar:
    start_ms: int
    end_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")

    def update(self, tick: Tick) -> None:
        self.high = max(self.high, tick.price)
        self.low = min(self.low, tick.price)
        self.close = tick.price
        self.volume += tick.quantity

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
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
