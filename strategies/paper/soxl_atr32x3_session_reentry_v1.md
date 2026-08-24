# SOXL ATR32×3 Session Re-entry v1

状态：仅批准独立 paper 前向观察；不用于实盘。

## 基线

- `SOXLUSDT` USD-M Futures，仅做多，15 分钟 K 线。
- Wilder ATR(32) × 3.0，TE(8) ≥ 0.25。
- `2x isolated × 62.5%`，目标名义敞口 1.25x。
- 单边手续费 5 bps，固定不利滑点 2 bps；信号后下一持久化 Tick 成交。

## 恢复重入

ATR 下穿止损不延迟：多仓仍立即提交 `reduce_only` 平仓。仅当退出信号位于以下北京时间
切换点的 ±30 分钟内，才武装一次恢复重入：

- 周日、周一 08:00；
- 周二至周四 16:00；
- 每日 21:30。

从退出成交后的下一根 15 分钟 K 线开始，最多观察两根 K 线。空仓、无待处理订单、当前
TE(8) ≥ 0.25，且价格达到以下阈值时重新开多：

```text
max(退出穿越前冻结的 ATR 止损线, 退出实际成交价 + 0.5 × 当前 ATR)
```

窗口过期即清除；普通 ATR 上穿可以先行开仓并清除恢复状态。状态必须持久化，服务重启不得
延长窗口或重复发单。

## 账本口径

账户 ID 为 `soxl_perp_long_session_reentry`，初始资金 100,000 USDT。重建起点固定为
2026-08-08 06:35:17.170 UTC（北京时间 14:35:17.170），与当前 `soxl_perp_long` 新策略账本
起点一致。研究依据见
[`reports/session_reentry/session_reentry_walk_forward_20260824.md`](../../reports/session_reentry/session_reentry_walk_forward_20260824.md)。
