# BTC/ETH Frozen Strategy Audit

审查对象：`btc-eth-expanding-calendar-router-v1`。审查使用本地恢复的 Binance
BTCUSDT/ETHUSDT USD-M 数据，覆盖 2020-01-01 至 2026-08-11；每个品种 231,840 根
15m K 线、7,212 条资金费记录，以及 3,764 个 Futures metrics 归档。2026-08 的
funding 月归档尚未发布，因此 8 月只能作为不完整检查期。

## 结论

月度锁没有发现 off-by-one 实现错误。当前实现的确定语义是：UTC 日线 D 收盘后检查
月收益；D 当天仍使用原敞口；若达到 `+18%` 或 `-20%`，D+1 日线开盘（下一条完整
UTC 日线）切换到零外层敞口，并在 D+1 计入退出换仓成本；新 UTC 月第一条日线解除锁定。
该行为由 [factor_overlay.py](../../../../src/mastermind_tick/factor_overlay.py:223)
和 [test_factor_overlay.py](../../../../tests/test_factor_overlay.py:102) 共同确认。

冻结文档虽写有“下一可交易时点”，但没有机器可审计的触发时间、触发日是否计入、
退出成本归属和锁事件输出字段。因此这是规格与审计缺口，不是当前回测锁定时机 bug。

## 2026 锁事件（分段确认口径）

| 账本 | 触发日 -> 下一空仓日 | 事件 |
|---|---|---|
| Base | Jan 14 -> Jan 15; Feb 4 -> Feb 5; Mar 4 -> Mar 5; Apr 13 -> Apr 14; May 15 -> May 16; Jun 3 -> Jun 4; Jul 6 -> Jul 7 | 6 profit, 1 loss |
| Stress | Jan 30 -> Jan 31; Feb 4 -> Feb 5; Mar 4 -> Mar 5; Apr 13 -> Apr 14; May 14 -> May 15; Jun 4 -> Jun 5; Jul 6 -> Jul 7 | 6 profit, 1 loss |

## 重要一致性问题

原报告的确认回放按年份切片，调用 `_year_returns` 时重置了前一月路由权重
([mine_expanding_calendar_router.py](../../../../scripts/research/mine_expanding_calendar_router.py:200))。
因此它不是连续状态回放。固定映射下，连续 2021-01 至 2026-08 回放得到：

| 账本 | 连续回放 2026-01 | 原报告/分段回放 2026-01 |
|---|---:|---:|
| Base | +17.8163% | +20.4135% |
| Stress | +27.2774% | +26.9009% |

2 月至 7 月基本一致，差异集中在跨 2025-12/2026-01 的路由换仓状态。该问题不属于
月度锁时机，但意味着原确认报告不能直接作为连续运行证据。

## 处置建议

在下一版策略前必须增加 `lock_effective_time`、`trigger_day_included`、
`exit_cost_day` 和逐事件锁日志；同时用连续状态回放重建确认报告。完成前保持
`paper only`，不应把现有确认收益解释为连续运行结果。
