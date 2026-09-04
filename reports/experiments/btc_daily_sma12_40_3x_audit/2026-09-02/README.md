# BTC Daily SMA12/40 Bear-Flat Audit (Hard 3X)

固定日线 SMA12/40：非熊市 1.5X，熊市 0X。信号在完成日线后下一根 15m 开盘执行。
账户按 50% 现货、50% 隔离 USD-M 抵押建模；合约开盘杠杆限制为 2.5X，盘中有效杠杆不得超过 3X。
压力成本为 10 bps 手续费、5 bps 滑点及历史 Funding。

## Full 2020–最新

| 指标 | 策略 | B&H |
|---|---:|---:|
| 收益 | 3085.68% | 972.87% |
| CAGR | 67.99% | 42.71% |
| 最大回撤 | -74.73% | -77.56% |
| 最高有效 Futures 杠杆 | 2.694X | - |

## Aggregate Splits

| 区间 | 策略 | B&H | 超额 |
|---|---:|---:|---:|
| research | 277.63% | 130.03% | 147.61% |
| validation | 576.41% | 465.68% | 110.73% |
| oos | 24.72% | -17.55% | 42.26% |
| full | 3085.68% | 972.87% | 2112.81% |

## Rolling Windows

| 窗口 | 数量 | 超过 B&H | 收益+DD 同胜 | 中位超额 | 最差超额 |
|---|---:|---:|---:|---:|---:|
| 1y | 70 | 68.57% | 60.00% | 21.74% | -76.86% |
| 2y | 57 | 80.70% | 57.89% | 34.64% | -87.52% |
| 3y | 45 | 84.44% | 73.33% | 124.47% | -142.53% |

## Bootstrap

- 7d: beat B&H 83.62%; joint return+DD 64.08%; annualized excess P05 -10.74%.
- 30d: beat B&H 85.48%; joint return+DD 67.13%; annualized excess P05 -8.59%.
- 90d: beat B&H 88.31%; joint return+DD 71.96%; annualized excess P05 -6.02%.

## Cost Sensitivity

| 情景 | Fee/边 | Slippage/边 | 收益 | 超额 | CAGR | DD |
|---|---:|---:|---:|---:|---:|---:|
| low | 5 bps | 2 bps | 3542.94% | 2570.08% | 71.40% | -74.11% |
| default | 10 bps | 5 bps | 3085.68% | 2112.81% | 67.99% | -74.73% |
| moderate | 20 bps | 10 bps | 2377.32% | 1404.45% | 61.78% | -75.86% |
| severe | 50 bps | 25 bps | 1065.59% | 92.73% | 44.49% | -79.09% |
| breakpoint | 75 bps | 40 bps | 494.90% | -477.96% | 30.64% | -81.71% |

结论：历史收益若超过 B&H，也不能替代未见数据；当前状态为 **RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。
