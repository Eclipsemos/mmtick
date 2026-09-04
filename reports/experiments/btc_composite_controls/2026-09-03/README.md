# BTC Composite Trend, Drawdown, and Funding Controls

固定比较 SMA10/40 迟滞、Funding 限制、回撤降仓及两种组合；不使用 OOS 选参。

| Candidate | Research超额 | Validation超额 | OOS超额 | Full CAGR | Full DD | 杠杆 | V vs 1.5X | 90d P05 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline-sma10-40-hysteresis` | 256.36% | 46.02% | 36.58% | 75.11% | -74.65% | 2.226X | -114.98% | 2.07% |
| `funding01` | 396.69% | 45.38% | 36.58% | 80.13% | -74.65% | 2.258X | -115.62% | - |
| `drawdown-look90-dd15-guard1` | 173.43% | 26.56% | 34.90% | 76.07% | -67.75% | 2.270X | -134.44% | - |
| `drawdown-look90-dd15-guard1-funding01` | 370.83% | 32.88% | 34.90% | 84.32% | -67.75% | 2.258X | -128.13% | 7.82% |
| `drawdown-look180-dd20-guard075-funding01` | 371.04% | 10.55% | 29.21% | 77.83% | -65.31% | 2.679X | -150.46% | - |

## Stress cost (full sample)

| Candidate | 50+25bps Full超额 | Full DD | 杠杆 |
|---|---:|---:|---:|
| `baseline-sma10-40-hysteresis` | 3277.82% | -78.84% | 2.227X |
| `funding01` | 900.66% | -78.84% | 2.260X |
| `drawdown-look90-dd15-guard1` | 3514.61% | -72.46% | 2.271X |
| `drawdown-look90-dd15-guard1-funding01` | 1935.72% | -72.46% | 2.260X |
| `drawdown-look180-dd20-guard075-funding01` | 1369.58% | -69.00% | 2.682X |

组合结论：
- 组合是否在全部区间超过 B&H：是。
- 组合是否满足严格 3X：是。
- 组合 Full 超额：21674.88%；最大回撤：-67.75%。
- 组合相对连续 1.5X 基准的 Validation 超额：-128.13%。
- 组合 90 日 bootstrap 年化超额 P05：7.82%。

状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。
