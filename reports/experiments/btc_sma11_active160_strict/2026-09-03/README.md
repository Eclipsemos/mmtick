# BTC SMA11/40 Active 1.60X Strict Audit

SMA11/40，连续 2 个熊市日进入 0X，1 个非熊市日恢复；主动暴露固定 1.60X。

| 区间 | 默认超额 | 50+25超额 | 75+40超额 | 默认CAGR | 默认DD | 杠杆 |
|---|---:|---:|---:|---:|---:|---:|
| research | 254.67% | 112.84% | 42.07% | 69.21% | -75.37% | 2.674X |
| validation | 197.99% | 43.46% | -42.71% | 176.15% | -52.12% | 2.783X |
| oos | 44.91% | 14.87% | -1.12% | 15.72% | -44.53% | 2.670X |
| full | 3647.68% | 961.76% | 84.76% | 78.21% | -75.37% | 2.783X |

## Bootstrap

- 7d: 跑赢 B&H 90.14%；年化超额 P05 -5.57%。
- 30d: 跑赢 B&H 91.94%；年化超额 P05 -3.36%。
- 90d: 跑赢 B&H 94.89%；年化超额 P05 -0.13%。

## 决策

```json
{
  "beats_bh_all_default_splits": true,
  "beats_bh_all_stress_splits": true,
  "hard_3x_passed": true,
  "bootstrap_90d_p05_positive": false
}
```

状态：**RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE**。
