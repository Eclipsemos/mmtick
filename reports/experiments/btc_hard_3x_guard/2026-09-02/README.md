# BTC Hard 3X Guard Research

候选使用完成的 4h 趋势与宏观 SMA，下一根 15m 开盘调仓；压力成本为 10+5 bps，计入 Funding。
硬约束要求所有分段的开盘与盘中低点观测杠杆均不超过 3X。

数据：244,857 根 15m；Funding 7,647 次；候选 32 个，硬约束通过 4 个，Research 与 Validation 均超过 B&H 的有 0 个。

## 开发期排名（仅 Research + Validation）

| 配置 | Research超额 | Validation超额 | OOS超额 | Full CAGR近似 | Full DD | 盘中最高杠杆 |
|---|---:|---:|---:|---:|---:|---:|
| `4h-26-52-104-208-macro1200-bear0.25x-bull1.5x-spot0.5` | 51.78% | -108.05% | 17.51% | 46.80% | -67.91% | 2.595X |
| `4h-26-52-104-208-macro900-bear0x-bull1.5x-spot0.5` | 45.67% | -138.67% | 18.58% | 45.02% | -66.29% | 2.595X |
| `4h-25-50-100-200-macro1200-bear0x-bull1.5x-spot0.5` | 36.00% | -145.67% | 18.38% | 43.84% | -65.60% | 2.595X |
| `4h-26-52-104-208-macro1200-bear0x-bull1.5x-spot0.5` | 48.09% | -146.61% | 21.82% | 45.48% | -65.65% | 2.595X |

## 解读

排名只用于提出候选；OOS 不能反向调参。即使 Full/OOS 超过 B&H，也必须通过独立留出、滚动窗口和前向观察，才可考虑 Paper Trading。

状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。
