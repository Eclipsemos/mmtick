# BTC Daily SMA10/40 Hysteresis Audit (Hard 3X)

固定日线 SMA10/40；进入熊市需连续 2 根 bearish 日线，恢复需 1 根 non-bearish 日线。
压力成本为 10 bps 手续费、5 bps 滑点及历史 Funding。

## Full 2020–最新

| 指标 | 策略 | B&H |
|---|---:|---:|
| 收益 | 4870.74% | 977.21% |
| CAGR | 79.61% | 42.81% |
| 最大回撤 | -70.20% | -77.56% |
| 最高有效 Futures 杠杆 | 0.598X | - |

## Aggregate Splits

| 区间 | 策略 | B&H | 超额 |
|---|---:|---:|---:|
| research | 505.16% | 130.03% | 375.14% |
| validation | 571.28% | 465.68% | 105.60% |
| oos | 22.36% | -17.21% | 39.58% |
| full | 4870.74% | 977.21% | 3893.53% |

## Rolling Windows

| 窗口 | 数量 | 超过 B&H | 收益+DD 同胜 | 中位超额 | 最差超额 |
|---|---:|---:|---:|---:|---:|
| 1y | 70 | 77.14% | 62.86% | 29.98% | -52.12% |
| 2y | 57 | 77.19% | 57.89% | 50.81% | -70.16% |
| 3y | 45 | 88.89% | 80.00% | 153.32% | -134.12% |

## Bootstrap

- 7d: beat B&H 91.80%; joint return+DD 73.42%; annualized excess P05 -4.06%.
- 30d: beat B&H 93.22%; joint return+DD 75.94%; annualized excess P05 -2.34%.
- 90d: beat B&H 94.99%; joint return+DD 79.62%; annualized excess P05 -0.01%.

结论：历史收益与回撤表现较强，但 Bootstrap 及未见前向数据仍是必要验证；状态为 **RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。
