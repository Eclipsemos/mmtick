# BTCUSDT MACD 背离多周期验证

5m 源数据：700,374 根，2020-01-01T00:00:00+00:00 至 2026-08-28T20:29:59.999000+00:00。

| 周期 | 冻结候选 | Research R | Validation R | OOS R | OOS PF | OOS 交易 |
|---:|---|---:|---:|---:|---:|---:|
| 5m | `rolling-20-2point-at_swing-atr2-rr1.5` | -0.206 | -0.264 | -0.323 | 0.663 | 2755 |
| 30m | `rolling-10-3point-at_swing-atr2-rr2.5` | -0.028 | -0.010 | -0.049 | 0.916 | 198 |
| 60m | `pivot-3-3-3point-at_swing-atr1-rr4` | 0.390 | 0.370 | -0.326 | 0.639 | 31 |
| 240m | `rolling-20-3point-at_swing-atr1.25-rr4` | 0.193 | 0.213 | 0.207 | 1.228 | 24 |

候选按 Research 与 Validation 排序，OOS 不参与选择。所有周期使用同一 5m 底层档案聚合；同柱冲突按 Stop 优先，费用与滑点沿用主报告默认值。
