# BTC Futures-Metric Matched-Benchmark Screen

BTC 期货市场指标独立于既有 SMA 规则筛选。所有信号仅使用已完成的 4 小时指标，并在下一根 15 分钟 K 线开盘交易。

| 配置 | R相对1.5X | V相对1.5X | 开发最差 | R相对1X | V相对1X |
|---|---:|---:|---:|---:|---:|
| `btc-global_crowding-window180-z1-fade-long-only` | 243.08% | -261.02% | -261.02% | 220.42% | -100.02% |
| `btc-top_retail_spread-window180-z1-follow-long-only` | 233.67% | -328.79% | -328.79% | 211.01% | -167.79% |
| `btc-top_account_crowding-window180-z1-fade-long-only` | 185.12% | -360.07% | -360.07% | 162.46% | -199.07% |
| `btc-global_crowding-window180-z1p5-fade-long-only` | 122.20% | -444.74% | -444.74% | 99.55% | -283.74% |
| `btc-top_retail_spread-window540-z1-follow-long-only` | 153.89% | -450.93% | -450.93% | 131.23% | -289.93% |
| `btc-top_account_crowding-window180-z1p5-fade-long-only` | 112.94% | -466.14% | -466.14% | 90.28% | -305.14% |
| `btc-top_retail_spread-window180-z1p5-follow-long-only` | 174.48% | -499.29% | -499.29% | 151.83% | -338.29% |
| `btc-global_crowding-window540-z1-fade-long-only` | 179.00% | -511.75% | -511.75% | 156.34% | -350.75% |
| `btc-global_crowding-window180-z2-fade-long-only` | 80.53% | -520.92% | -520.92% | 57.88% | -359.92% |
| `btc-global_crowding-window1080-z1-fade-long-only` | 152.94% | -521.54% | -521.54% | 130.28% | -360.54% |
| `btc-top_retail_spread-window1080-z1-follow-long-only` | 151.97% | -530.06% | -530.06% | 129.31% | -369.06% |
| `btc-top_account_crowding-window1080-z1p5-fade-long-only` | 147.07% | -553.75% | -553.75% | 124.42% | -392.75% |
| `btc-top_account_crowding-window540-z1-fade-long-only` | 201.18% | -554.45% | -554.45% | 178.52% | -393.45% |
| `btc-top_retail_spread-window540-z1p5-follow-long-only` | 116.12% | -554.56% | -554.56% | 93.46% | -393.56% |
| `btc-global_crowding-window1080-z1p5-fade-long-only` | 123.55% | -560.82% | -560.82% | 100.90% | -399.82% |
| `btc-top_account_crowding-window1080-z1-fade-long-only` | 173.04% | -563.09% | -563.09% | 150.38% | -402.09% |
| `btc-top_retail_spread-window180-z2-follow-long-only` | 83.74% | -565.62% | -565.62% | 61.08% | -404.62% |
| `btc-top_retail_spread-window1080-z1p5-follow-long-only` | 124.75% | -566.26% | -566.26% | 102.09% | -405.26% |
| `btc-oi_change_24h-window540-z1-fade-long-only` | 66.72% | -579.91% | -579.91% | 44.06% | -418.91% |
| `btc-top_account_crowding-window180-z2-fade-long-only` | 110.60% | -582.80% | -582.80% | 87.94% | -421.80% |

Markdown 仅显示按开发期最差表现排序的前 20 个；完整 144 个配置见 `results.json`。
开发期合格成员：0 / 144。
只有开发期同时超过连续 1.5X BTC、无强平且盘中杠杆不超过 3X 的成员才读取 OOS；没有合格成员时，2025 之后结果不会被用于反向挑选。
