# BTC Spot-Perpetual Cash-and-Carry Screen

自有现金买入 BTC 现货并等数量做空永续，不借币；以已完成 4h basis 与已结算 Funding 决定下一根 15m 开盘是否持有。

| 配置 | R超额 | V超额 | 开发最差 | R DD | V DD |
|---|---:|---:|---:|---:|---:|
| `cash-carry-basis-ge-20p000bps-funding-ge-0bps` | -140.51% | -466.59% | -466.59% | -10.51% | -0.73% |
| `cash-carry-basis-ge-20p000bps-funding-ge-0p250000bps` | -140.33% | -466.59% | -466.59% | -10.33% | -0.73% |
| `cash-carry-basis-ge-20p000bps-funding-ge-0p50000bps` | -140.33% | -466.59% | -466.59% | -10.33% | -0.73% |
| `cash-carry-basis-ge-20p000bps-funding-ge-1p0000bps` | -140.33% | -466.59% | -466.59% | -10.33% | -0.73% |
| `cash-carry-basis-ge-10p000bps-funding-ge-1p0000bps` | -176.89% | -477.40% | -477.40% | -47.19% | -11.44% |
| `cash-carry-basis-ge-10p000bps-funding-ge-0bps` | -176.83% | -477.65% | -477.65% | -47.13% | -11.69% |
| `cash-carry-basis-ge-10p000bps-funding-ge-0p250000bps` | -176.89% | -477.65% | -477.65% | -47.19% | -11.69% |
| `cash-carry-basis-ge-10p000bps-funding-ge-0p50000bps` | -176.89% | -477.65% | -477.65% | -47.19% | -11.69% |
| `cash-carry-basis-ge-0bps-funding-ge-0p50000bps` | -164.28% | -482.64% | -482.64% | -34.47% | -19.78% |
| `cash-carry-basis-ge-0bps-funding-ge-0p250000bps` | -164.28% | -482.65% | -482.65% | -34.47% | -19.79% |
| `cash-carry-basis-ge-0bps-funding-ge-0bps` | -164.38% | -482.66% | -482.66% | -34.57% | -19.79% |
| `cash-carry-basis-ge-0bps-funding-ge-1p0000bps` | -163.53% | -482.69% | -482.69% | -33.91% | -19.82% |
| `cash-carry-basis-ge-5p0000bps-funding-ge-0bps` | -171.02% | -491.25% | -491.25% | -41.63% | -27.41% |
| `cash-carry-basis-ge-5p0000bps-funding-ge-0p250000bps` | -171.08% | -491.25% | -491.25% | -41.69% | -27.41% |
| `cash-carry-basis-ge-5p0000bps-funding-ge-0p50000bps` | -171.08% | -491.25% | -491.25% | -41.69% | -27.41% |
| `cash-carry-basis-ge-5p0000bps-funding-ge-1p0000bps` | -171.08% | -491.29% | -491.29% | -41.69% | -27.45% |

开发期合格成员：0 / 16。
只有开发期同时超过 BTC 现货 B&H、无破产且盘中总名义杠杆不超过 3X 的成员才读取 OOS。
