# BTC Stitched SMA10/40 Hysteresis Audit (Hard 3X)

固定日线 SMA10/40；连续 2 根 bearish 日线才进入熊市，连续 1 根 non-bearish 日线恢复。
现货 2017–2019 与 USD-M 2020–最新拼接，压力成本为 10 bps 手续费、5 bps 滑点及 Funding。

## Stitched Full

| 指标 | 策略 | B&H |
|---|---:|---:|
| 收益 | 14792.64% | 1665.45% |
| 最大回撤 | -74.65% | -83.48% |
| 最高盘中有效杠杆 | 2.456X | - |

## Segments

| 区间 | 策略 | B&H | 超额 | DD |
|---|---:|---:|---:|---:|
| spot_pre2020 | 317.29% | 64.33% | 252.96% | -74.65% |
| 2020_2022 | 382.65% | 130.03% | 252.62% | -69.77% |
| 2023_2024 | 516.11% | 465.68% | 50.43% | -50.14% |
| 2025_latest | 19.62% | -17.37% | 36.99% | -41.19% |
| stitched_full | 14792.64% | 1665.45% | 13127.20% | -74.65% |

## Rolling Windows

| 窗口 | 数量 | 超过 B&H | 收益+DD 同胜 | 中位超额 | 最差超额 |
|---|---:|---:|---:|---:|---:|
| 1y | 97 | 80.41% | 72.16% | 22.95% | -186.15% |
| 2y | 85 | 80.00% | 72.94% | 53.23% | -88.93% |
| 3y | 73 | 91.78% | 86.30% | 247.15% | -151.25% |

## Bootstrap

- 7d: beat B&H 93.31%; joint return+DD 77.74%; annualized excess P05 -2.41%.
- 30d: beat B&H 94.60%; joint return+DD 79.89%; annualized excess P05 -0.64%.
- 90d: beat B&H 96.61%; joint return+DD 81.37%; annualized excess P05 2.33%.
- 180d: beat B&H 98.79%; joint return+DD 86.69%; annualized excess P05 7.05%.
- 365d: beat B&H 99.35%; joint return+DD 91.82%; annualized excess P05 8.84%.
- 730d: beat B&H 99.55%; joint return+DD 95.54%; annualized excess P05 9.29%.

## Tail concentration

| Removed best relative days | Strategy CAGR | B&H CAGR | Excess |
|---:|---:|---:|---:|
| 0 | 75.20% | 37.96% | 27.00% |
| 1 | 75.20% | 46.08% | 19.93% |
| 5 | 75.20% | 58.09% | 10.82% |
| 10 | 75.28% | 70.71% | 2.68% |
| 20 | 75.28% | 93.10% | -9.22% |

结论：90–730 日区块 Bootstrap 的正超额下界已为正，支持中周期机制；但 7/30 日下界仍为负，且日线聚合执行不能替代严格 15m 风控审计。
状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。
