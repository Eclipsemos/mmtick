# 小果量化 ATR 收盘确认策略

## 策略概述

- 周期：15 分钟 K 线
- ATR：TradingView `ta.atr(7)`，即 Wilder RMA
- ATR Multiplier：`1.0`
- 方向：仅做多，`pyramiding = 0`
- 仓位：默认使用账户权益的 100%
- 实时计算：每个 Tick 更新当前 K 线、ATR 和移动止损线
- 交易确认：仅在 15 分钟 K 线正式收盘时判断穿越
- 成交时点：信号 K 线的下一根 K 线开盘

系统中的“下一根开盘”取 Binance 新 K 线内收到的第一笔实际行情。SOXL Futures 行情按 feed 的 250 ms 聚合批次处理，因此取新 K 线首个聚合批次。

## ATR 移动止损线

止损距离：

```text
sl_value = ATR(7) × 1.0
```

移动止损线严格按 Pine 表达式计算：

```text
source > tsl[1] 且 source[1] > tsl[1]
    → max(tsl[1], source - sl_value)
source < tsl[1] 且 source[1] < tsl[1]
    → min(tsl[1], source + sl_value)
source > tsl[1]
    → source - sl_value
其他
    → source + sl_value
```

实时 K 线内，`source` 是不断更新的 close；`source[1]` 与 `tsl[1]` 始终来自上一根已收盘 K 线。因此实时 ATR 线会随成交 Tick 变化，但不会在盘中触发交易。交易确认使用 Binance 官方 `@kline_15m` 收盘事件，并通过 REST `/klines` 校验最终 OHLCV。

## 交易规则

买入确认：

```text
当前 K 线收盘价 > 当前 K 线最终 ATR 止损线
且上一根收盘价 <= 上一根 ATR 止损线
且当前空仓
```

平仓确认：

```text
当前 K 线收盘价 < 当前 K 线最终 ATR 止损线
且上一根收盘价 >= 上一根 ATR 止损线
且当前持有多头
```

等价于 Pine 的：

```text
ta.crossover(close, tsl_price)
ta.crossunder(close, tsl_price)
```

当前 K 线收盘时生成订单，下一根 K 线首笔行情模拟成交。系统不建立空头，不使用 Tick 防抖，也不需要单根 K 线交易锁；一根 K 线只有一个最终收盘状态，天然只会确认一个方向。

## 信号与成交记录

- `signal_price`：确认信号 K 线的收盘价
- `trailing_stop`：确认信号 K 线的最终 ATR 止损线
- `atr`：确认信号 K 线的最终 ATR
- `submitted_at_ms`：确认信号 K 线结束时间
- Fill 时间与价格：下一根 K 线第一笔实际行情的时间和价格，再应用模拟滑点

这对应 Pine `process_orders_on_close = false`：收盘确认产生订单，下一根 K 线开盘成交。

## 实时状态

页面持续显示当前价格、实时 ATR、黄色 ATR 止损线、价格距离、当前仓位和 K 线结束时间。当前 K 线形成期间会明确显示“不交易”；最近确认信号显示确认方向、结果和收盘时间。

## 回测一致性

交易判断只使用完整 15 分钟 OHLCV 的收盘结果，不依赖历史 K 线内部 Tick 顺序，因此比盘中 Tick 穿越策略更容易与 TradingView 历史回测对齐。实时线仍可能在未收盘 K 线内变化，这是 `calc_on_every_tick = true` 的显示语义，不代表已产生交易信号。
