# BTC 免费再平衡模型审计

旧增量引擎每根15m K线按目标杠杆复合收益，但只在信号变化时收费，等价于免费连续再平衡。新权威模型只在稀疏目标变化时交易，期间持仓数量固定。

确定性复现：继承2X仓位、价格 `100 -> 110 -> 121`，旧模型+44%，固定数量应为+42%。

| 候选 | 分段 | 成本 | 固定数量 | 旧模型 | 旧减新 |
|---|---|---|---:|---:|---:|
| `primary` | full | base | 2166.49% | 2211.60% | 45.11% |
| `primary` | full | stress | 1528.58% | 1563.37% | 34.79% |
| `primary` | oos | base | 9.43% | 8.27% | -1.15% |
| `primary` | oos | stress | 2.29% | 1.23% | -1.06% |
| `partial_bear_challenger` | full | base | 2813.90% | 3165.40% | 351.50% |
| `partial_bear_challenger` | full | stress | 2068.19% | 2335.06% | 266.86% |
| `partial_bear_challenger` | oos | base | 0.36% | -1.05% | -1.41% |
| `partial_bear_challenger` | oos | stress | -4.80% | -6.11% | -1.31% |
| `equal_weight_ensemble` | full | base | 2522.91% | 2783.07% | 260.16% |
| `equal_weight_ensemble` | full | stress | 1819.24% | 2013.75% | 194.51% |
| `equal_weight_ensemble` | oos | base | 5.37% | 4.29% | -1.08% |
| `equal_weight_ensemble` | oos | stress | -0.74% | -1.73% | -0.99% |

旧模型不再允许用于候选批准。所有冻结指标、Walk-Forward、滚动窗口、Bootstrap和归因报告均以固定数量模型为准。
