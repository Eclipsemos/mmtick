# BTC 杠杆维持保证金缓冲审计

按近似交叉保证金模型检查账户权益是否高于维持保证金。这是风险筛查，不是交易所清算价保证。

| 候选 | 最大敞口 | 费率 | 最大保证金占用 | 最小继续下跌空间 | 缓冲≤0K线 |
|---|---:|---:|---:|---:|---:|
| `primary` | 1.5X | 0.004 | 0.23% | 57.75% | 0 |
| `primary` | 1.5X | 0.005 | 0.29% | 57.73% | 0 |
| `primary` | 1.5X | 0.01 | 0.58% | 57.66% | 0 |
| `primary` | 1.5X | 0.02 | 1.15% | 57.52% | 0 |
| `partial_bear_challenger` | 1.75X | 0.004 | 0.38% | 45.58% | 0 |
| `partial_bear_challenger` | 1.75X | 0.005 | 0.47% | 45.55% | 0 |
| `partial_bear_challenger` | 1.75X | 0.01 | 0.94% | 45.44% | 0 |
| `partial_bear_challenger` | 1.75X | 0.02 | 1.88% | 45.20% | 0 |
| `equal_weight_ensemble` | 1.625X | 0.004 | 0.30% | 51.17% | 0 |
| `equal_weight_ensemble` | 1.625X | 0.005 | 0.38% | 51.15% | 0 |
| `equal_weight_ensemble` | 1.625X | 0.01 | 0.75% | 51.05% | 0 |
| `equal_weight_ensemble` | 1.625X | 0.02 | 1.50% | 50.86% | 0 |

近似公式：futures maintenance = maintenance_rate × (exposure−1) × equity；实际交易所还会使用标记价格、分层维持保证金、清算费和ADL。
任何真实部署前必须按账户实际保证金模式和名义金额重新计算。
