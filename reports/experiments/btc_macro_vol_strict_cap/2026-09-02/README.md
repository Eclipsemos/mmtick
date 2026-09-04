# BTC Macro/Volatility Strategy — Strict 3X Audit

波动率缩放候选使用 2.5X 开盘上限，为盘中权益变化保留缓冲；任何 Research/Validation 盘中有效杠杆超过 3X 的候选均淘汰。

## Selected candidate

`4h-24-48-96-192-macro1200-vol360-target1-max2.5x-strict3x`；开发期合格候选 9 / 15。

| 区间 | 策略收益 | B&H | 超额 | 策略DD | 盘中最高杠杆 |
|---|---:|---:|---:|---:|---:|
| research | 182.99% | 130.03% | 52.96% | -73.48% | 2.546X |
| validation | 514.63% | 465.68% | 48.95% | -35.81% | 2.590X |
| oos | -11.18% | -17.30% | 6.12% | -54.41% | 2.565X |
| full | 1450.00% | 976.09% | 473.91% | -73.48% | 2.590X |

Full CAGR：50.80%；B&H CAGR：42.78%。

## Bootstrap

- 7d: 超过 B&H 67.29%；年化超额 P05 -12.76%。
- 30d: 超过 B&H 65.89%；年化超额 P05 -13.29%。
- 90d: 超过 B&H 66.25%；年化超额 P05 -12.59%。

结论：历史/开发与 OOS 结果若为正，只能说明候选值得前向观察；尚未证明统计显著或适合实盘。
状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。
