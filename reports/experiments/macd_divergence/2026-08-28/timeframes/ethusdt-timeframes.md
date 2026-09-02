# ETHUSDT MACD 背离多周期验证

5m 源数据：700,374 根，2020-01-01T00:00:00+00:00 至 2026-08-28T20:29:59.999000+00:00。

| 周期 | 冻结候选 | Research R | Validation R | OOS R | OOS PF | OOS 交易 |
|---:|---|---:|---:|---:|---:|---:|
| 5m | `rolling-10-2point-at_swing-atr2-rr4` | -0.170 | -0.187 | -0.137 | 0.836 | 1084 |
| 30m | `rolling-20-2point-at_swing-atr1.25-rr4` | -0.024 | -0.010 | 0.020 | 0.999 | 266 |
| 60m | `pivot-3-3-2point-at_swing-atr1.25-rr2` | 0.004 | 0.015 | -0.026 | 0.951 | 165 |
| 240m | `rolling-10-3point-at_swing-atr0.5-rr4` | 0.232 | 0.203 | -0.106 | 0.857 | 37 |

候选按 Research 与 Validation 排序，OOS 不参与选择。所有周期使用同一 5m 底层档案聚合；同柱冲突按 Stop 优先，费用与滑点沿用主报告默认值。
