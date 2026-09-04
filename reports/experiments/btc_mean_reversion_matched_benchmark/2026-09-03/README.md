# BTC Daily Mean Reversion vs Matched 1.5X Benchmark

预先定义的 Bollinger 和 RSI 日线回归规则均与持续 1.5X BTC 使用相同回放模型。

| 配置 | 家族 | R相对1.5X | V相对1.5X | 开发最差 | R DD | V DD |
|---|---|---:|---:|---:|---:|---:|
| `daily-bollinger-50-2-long-only-active1.5x` | bollinger | -96.09% | -459.07% | -459.07% | -82.32% | -23.19% |
| `daily-bollinger-50-1.5-long-only-active1.5x` | bollinger | -105.39% | -481.92% | -481.92% | -83.63% | -23.19% |
| `daily-bollinger-20-2-long-only-active1.5x` | bollinger | -99.94% | -533.43% | -533.43% | -73.71% | -39.56% |
| `daily-rsi-14-35-long-only-active1.5x` | rsi | -59.16% | -539.72% | -539.72% | -73.17% | -29.48% |
| `daily-bollinger-20-1.5-long-only-active1.5x` | bollinger | -89.30% | -551.86% | -551.86% | -73.71% | -40.22% |
| `daily-rsi-14-30-long-only-active1.5x` | rsi | 23.18% | -568.60% | -568.60% | -50.14% | -23.19% |
| `daily-rsi-21-35-long-only-active1.5x` | rsi | -8.25% | -594.24% | -594.24% | -66.92% | -23.19% |
| `daily-rsi-21-30-long-only-active1.5x` | rsi | 3.97% | -624.31% | -624.31% | -58.97% | -15.82% |

开发期合格成员：0 / 8。
OOS 只对开发期合格成员计算。
