# 小果量化 ATR Tick 实时策略

## 策略概述

- 周期：15 分钟 K 线
- ATR：TradingView `ta.atr(7)` 对应的 Wilder RMA
- ATR Multiplier：`1.0`
- 方向：SOXLB 现货仅做多；SOXL 永续双向反手
- 仓位：现货使用 100% 权益；永续使用 95% 保证金、2x 杠杆
- 信号检测：每个 Binance 成交 Tick
- 成交时点：信号后的下一成交 Tick
- 防抖：无时间防抖

SOXLB 使用 Binance `aggTrade`；SOXL Futures 使用 250 ms 成交批次。两者都会实时更新当前 15 分钟 K 线、ATR 和移动止损线。

## ATR 移动止损线

止损距离：

```text
sl_value = ATR(7) x 1.0
```

移动止损线按 `25784e3` 原始实现递归计算：

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

启动时使用 Binance 官方历史 K 线预热。运行后由成交 Tick 合成当前 15 分钟 OHLCV；进入下一根 K 线时，将上一根 Tick 合成 K 线加入 ATR 序列。每个 Tick 先使用更新前的 `previous_price` 和 `previous_stop` 判断穿越，再用当前 Tick 价格和实时 ATR 递归更新止损线。

## 交易规则

上穿买入：

```text
上一 Tick 价格 <= 更新前的 ATR 线
且当前 Tick 价格 > 更新前的 ATR 线
且现货当前空仓，或永续当前为空仓/空头
且本 K 线未买入、未卖出
```

下穿卖出：

```text
上一 Tick 价格 >= 更新前的 ATR 线
且当前 Tick 价格 < 更新前的 ATR 线
且现货当前持有多头，或永续当前为空仓/多头
且本 K 线未卖出
```

SOXLB 的 SELL 只平掉多头。SOXL 永续的 SELL 平多后以 2x 反手做空，BUY 平空后以 2x 反手做多；反手拆为 `CLOSE` 和 `OPEN` 两条成交腿。卖出后本根 K 线禁止重新买入；买入与卖出各自最多触发一次。新 15 分钟 K 线开始后交易锁重置。

## 官方 K 线仓库

Binance `@kline_15m` 提供收盘事件，随后系统通过 REST `/klines` 校验最终 OHLCV：

- REST 值覆盖仓库内成交流形成的临时 OHLCV；
- WebSocket 与 REST 不一致时记录 `KLINE_RECONCILED`；
- REST 尚未返回或请求失败时记录仓库异常，但不暂停 Tick 策略；
- 官方收盘 K 线不改写运行中的策略状态，也不产生交易信号。

## 信号与成交记录

- `signal_price`：触发穿越的 Tick 价格
- `trailing_stop`：该 Tick 的实时 ATR 止损线
- `atr`：该 Tick 的实时 ATR
- `submitted_at_ms`：信号 Tick 时间
- Fill：订单在下一成交 Tick 模拟成交，再应用手续费和滑点

页面持续显示实时 ATR、黄色 ATR 止损线、Tick 穿越结果、交易锁、官方 K 线及 REST 校验状态。
