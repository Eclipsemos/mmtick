# 小果量化 ATR Tick 实时策略

## 策略概述

- 周期：15 分钟 K 线
- ATR：TradingView `ta.atr(7)` 对应的 Wilder RMA
- ATR Multiplier：`1.0`
- 方向：仅做多，`pyramiding = 0`
- 仓位：默认使用账户权益的 100%
- 信号检测：每个 Binance 成交 Tick
- 成交时点：信号 Tick
- 防抖：无时间防抖

SOXLB 使用 Binance `aggTrade`；SOXL Futures 使用 250 ms 成交批次。两者都会实时更新当前 15 分钟 K 线、ATR 和移动止损线。

## ATR 移动止损线

止损距离：

```text
sl_value = ATR(7) x 1.0
```

移动止损线严格按 Pine 表达式计算：

```text
source > tsl[1] 且 source[1] > tsl[1]
    -> max(tsl[1], source - sl_value)
source < tsl[1] 且 source[1] < tsl[1]
    -> min(tsl[1], source + sl_value)
source > tsl[1]
    -> source - sl_value
其他
    -> source + sl_value
```

实时 K 线内，`source` 是当前成交价；`source[1]`、`tsl[1]` 和上一 ATR 基线来自最新一根经 REST 校验的 Binance 官方 15 分钟 K 线。每个 Tick 都从该基线重新计算当前 ATR 线，匹配 Pine `calc_on_every_tick = true` 的实时语义。

## 交易规则

买入：

```text
当前 Tick 价格 >= 当前 ATR 线
且上一 Tick 在 ATR 线下方
且当前空仓、没有待成交单
且本 K 线未买入、未卖出
```

平仓：

```text
当前 Tick 价格 < 当前 ATR 线
且上一 Tick 在 ATR 线上方
且当前持有多头、没有待成交单
且本 K 线未卖出
```

策略不会建立空头。卖出后本根 K 线禁止重新买入；买入与卖出各自最多触发一次。新 15 分钟 K 线开始后交易锁重置。

## 官方 K 线校准

Binance `@kline_15m` 提供收盘事件，随后系统通过 REST `/klines` 校验最终 OHLCV：

- REST 值覆盖成交流形成的临时 OHLCV；
- WebSocket 与 REST 不一致时记录 `KLINE_RECONCILED`；
- REST 尚未返回或请求失败时暂停新信号，并由后续 Tick 重试；
- 官方 K 线只校准下一根 K 线的 ATR 基线，不产生收盘交易信号。

## 信号与成交记录

- `signal_price`：触发穿越的 Tick 价格
- `trailing_stop`：该 Tick 的实时 ATR 止损线
- `atr`：该 Tick 的实时 ATR
- `submitted_at_ms`：信号 Tick 时间
- Fill：同一 Tick 模拟成交，再应用手续费和滑点

页面持续显示实时 ATR、黄色 ATR 止损线、Tick 穿越结果、交易锁、官方 K 线及 REST 校验状态。
