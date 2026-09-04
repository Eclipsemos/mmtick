# BTC Daily SMA Ensemble + Funding Gate — Strict 3X

开发期选择：`funding>0.0002`；合格候选 5 / 5。
Funding 过滤只使用当时已公布的最近费率；额外暴露在高 Funding 时降为 1X。

| 区间 | 策略 | B&H | 超额 | DD | 盘中最高杠杆 |
|---|---:|---:|---:|---:|---:|
| research | 406.91% | 130.03% | 276.88% | -74.41% | 2.599X |
| validation | 545.16% | 465.68% | 79.49% | -48.01% | 2.694X |
| oos | 24.06% | -17.30% | 41.36% | -35.59% | 2.301X |
| full | 3959.09% | 976.09% | 2983.01% | -74.41% | 2.694X |

Full CAGR：74.21%；B&H CAGR：42.78%。

## Threshold ranking

- `funding>0.0002`：development score 16.14%；Research/Validation 均通过 True。
- `funding>0.0001`：development score 10.89%；Research/Validation 均通过 True。
- `no-gate`：development score 9.76%；Research/Validation 均通过 True。
- `funding>0.0003`：development score 8.36%；Research/Validation 均通过 True。
- `funding>0.00015`：development score 7.38%；Research/Validation 均通过 True。

## Bootstrap

- 7d: 超过 B&H 87.18%；年化超额 P05 -7.64%。
- 30d: 超过 B&H 88.73%；年化超额 P05 -6.88%。
- 90d: 超过 B&H 91.93%；年化超额 P05 -3.42%。

## Rolling windows

- 1y: 超过 B&H 74.29%；收益与 DD 同胜 60.00%；最差超额 -56.53%。
- 2y: 超过 B&H 75.44%；收益与 DD 同胜 56.14%；最差超额 -68.09%。
- 3y: 超过 B&H 84.44%；收益与 DD 同胜 73.33%；最差超额 -151.47%。

状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。
