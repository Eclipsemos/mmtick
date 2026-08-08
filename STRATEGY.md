# 当前交易策略

本文档描述 `mastermind:tick` 当前启用的 `SOXLUSDT` 策略。实际运行参数以
[`config/settings.toml`](config/settings.toml) 为准。

## 策略参数

| 项目 | 当前值 |
|---|---:|
| 方向 | 仅做多 |
| 信号周期 | 15 分钟 |
| ATR | Wilder ATR(32) |
| ATR 跟踪倍数 | 3.0 |
| 趋势效率窗口 | 8 根 K 线 |
| 最低趋势效率 | 0.25 |
| 盈利保护 | 关闭 |
| 延续重入 | 关闭 |
| 交易所杠杆 | 2x isolated |
| 仓位预算 | 账户权益的 62.5% |
| 目标名义敞口 | 账户权益的 1.25x |

## 杠杆与仓位优化

在保持信号参数不变的条件下，风险预算搜索覆盖 1x 至 4x 交易所杠杆和 25% 至 75% 仓位比例，
并在 2x 杠杆下对 62.5% 至 75% 做细化。选择标准是在训练和验证均盈利的前提下，取验证集
最大回撤不超过 30% 的最高名义敞口。

| 项目 | 优化推荐值 |
|---|---:|
| 交易所杠杆 | 2x isolated |
| 仓位预算 | 账户权益的 70% |
| 目标名义敞口 | 账户权益的 1.40x |
| 风险边界 | 验证集最大回撤不超过 30% |

2x、70% 仓位的完整连续历史收益为 `+202.13%`，最大回撤 `-29.42%`；7 月 31 日至
8 月 7 日留出集收益为 `+18.87%`，最大回撤 `-15.93%`。胜率仍为 `42.28%`，因为杠杆和
仓位只改变每笔交易的资金规模，不改变信号及输赢次数。

| 风险预算 | 名义敞口 | 完整收益 | 最大回撤 | 留出集收益 |
|---|---:|---:|---:|---:|
| 2x × 50% | 1.00x | +131.26% | -21.98% | +13.88% |
| 2x × 62.5%（当前运行） | 1.25x | +174.47% | -26.71% | +17.04% |
| **2x × 70%（优化推荐）** | **1.40x** | **+202.13%** | **-29.42%** | **+18.87%** |

2x、72.5% 仓位在验证集的最大回撤已经达到 `-30.30%`，2x、75% 为 `-31.18%`，因此没有
继续提高仓位。采用更高交易所杠杆但维持相同名义敞口不会提高收益，只会减少保证金和强平
缓冲，所以选择能够实现 1.40x 敞口的最低整数杠杆 2x。

该推荐值目前只记录在策略文档中，尚未写入 `config/settings.toml` 或应用到 paper / 实盘。
当前运行值仍是 2x、62.5%。原始结果见
[`optimization_stage21_long_risk_budget_train_validation.json`](reports/soxl_perp_full_history/optimization_stage21_long_risk_budget_train_validation.json)、
[`optimization_stage22_long_risk_budget_refined.json`](reports/soxl_perp_full_history/optimization_stage22_long_risk_budget_refined.json) 和
[`optimization_stage23_long_risk_budget_finalists.json`](reports/soxl_perp_full_history/optimization_stage23_long_risk_budget_finalists.json)。

## 信号规则

策略使用 Binance 官方 15 分钟 K 线完成 200 根预热，随后由每个成交 Tick 更新当前 K 线、
Wilder ATR 和递归 ATR 跟踪线。

ATR 距离为：

```text
ATR distance = Wilder ATR(32) × 3.0
```

开仓必须同时满足：

1. 当前没有持仓和待处理订单。
2. 价格从 ATR 跟踪线下方穿越到上方。
3. 最近 8 根 K 线的趋势效率不低于 0.25。
4. 当前 15 分钟 K 线尚未执行过其他交易动作。

持有多仓时，价格从 ATR 跟踪线上方穿越到下方，策略发送 `reduce_only` 平仓信号。平仓后保持
空仓，等待下一次有效向上穿越；不会建立空仓，也不会在下一根 K 线自动反手。

Paper 新账户可执行一次启动趋势对齐：若价格已位于 ATR 线上方且趋势过滤通过，则建立多仓。
实盘启动不会追入现有趋势，只等待新的有效穿越。策略状态持久化，普通重启不会重复启动入场。

## 成交与风控

- 每根 15 分钟 K 线最多执行一个策略动作。
- Paper 在信号后的下一笔持久化 Tick 成交，Futures 使用 5 bps 手续费和 2 bps 滑点。
- 实盘采用 Binance 返回的实际订单与成交结果，开仓盘口偏离上限为 30 bps。
- 实盘长仓模式会拒绝非减仓开空信号；若账户已经存在空仓，策略下单门禁会保持阻断。
- ATR 退出由服务收到实时 Tick 后触发，不是 Binance 托管止损。服务、网络或行情中断期间不能
  执行策略退出。
- 当前 `profit_activation_atr`、`profit_trailing_atr` 和 `continuation_reentry_atr` 均为 0，
  对应功能关闭。

## 历史验证

数据覆盖 Binance `SOXLUSDT` 上市以来至 2026-08-07 13:13:15 UTC。前 200 根 15 分钟 K 线
仅用于预热。

| 区间 | 收益 | 最大回撤 |
|---|---:|---:|
| 2026-05-17 至 05-31 | +33.32% | -14.66% |
| 2026-06 | +36.41% | -20.65% |
| 2026-07 | +22.67% | -26.71% |
| 2026-08-01 至 08-07 | +18.11% | -14.34% |
| 完整连续历史 | +174.47% | -26.71% |

完整连续历史从 100,000 USDT 增长至 274,471.47 USDT，共完成 123 轮交易，胜率 42.28%，
利润因子 1.48。7 月标的价格约下跌 58%，策略通过及时平仓并在主要下跌阶段保持空仓，取得
22.67% 收益。

详细优化方法、候选比较和限制见
[`reports/soxl_perp_full_history/final_optimization_20260807.md`](reports/soxl_perp_full_history/final_optimization_20260807.md)。
不固定周期和趋势窗口的复核见
[`reports/soxl_perp_full_history/multitimeframe_optimization_20260807.md`](reports/soxl_perp_full_history/multitimeframe_optimization_20260807.md)。

## 限制

合约上市历史不足三个月，8 月样本只有约一周。回放没有完整模拟盘口深度、API 延迟、交易所
拒单、服务中断和强制清算。历史结果用于验证当前实现，不构成未来收益保证。
