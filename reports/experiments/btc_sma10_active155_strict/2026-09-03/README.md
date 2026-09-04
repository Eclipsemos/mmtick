# BTC SMA10/40 Active 1.55X Strict Audit

SMA10/40，连续 3 个熊市日进入 0X，1 个非熊市日恢复；主动暴露固定 1.55X。

| 区间 | 默认超额 | 50+25超额 | 75+40超额 | 默认CAGR | 默认DD | 杠杆 |
|---|---:|---:|---:|---:|---:|---:|
| research | 104.80% | 18.10% | -26.89% | 49.59% | -77.13% | 2.699X |
| validation | 134.06% | 17.47% | -50.18% | 164.35% | -54.24% | 2.783X |
| oos | 23.00% | 0.27% | -12.10% | 3.38% | -50.36% | 2.505X |
| full | 1400.49% | 124.45% | -336.86% | 61.77% | -77.13% | 2.783X |

## Bootstrap

- 7d: 跑赢 B&H 76.74%；年化超额 P05 -14.37%。
- 30d: 跑赢 B&H 78.74%；年化超额 P05 -12.60%。
- 90d: 跑赢 B&H 81.02%；年化超额 P05 -9.60%。

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
