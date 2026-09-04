# BTC Funding-Event Matched-Benchmark Screen

Funding 结算事件只按此前事件归一化，信号在完成 4h K 线后、下一根 15m 开盘执行。

| 配置 | R相对1.5X | V相对1.5X | 开发最差 | R相对1X | V相对1X |
|---|---:|---:|---:|---:|---:|
| `funding-event-reversal-long_only-lookback-90-threshold-1-hold-12x4h` | 35.35% | -409.27% | -409.27% | -61.43% | -248.27% |
| `funding-event-reversal-long_only-lookback-30-threshold-1-hold-12x4h` | -11.12% | -425.91% | -425.91% | -107.90% | -264.91% |
| `funding-event-reversal-long_only-lookback-90-threshold-1p5-hold-8x4h` | 42.00% | -431.34% | -431.34% | -54.78% | -270.34% |
| `funding-event-continuation-long_only-lookback-180-threshold-1-hold-12x4h` | -57.37% | -452.31% | -452.31% | -154.15% | -291.31% |
| `funding-event-reversal-long_only-lookback-30-threshold-1-hold-8x4h` | 48.26% | -456.52% | -456.52% | -48.52% | -295.51% |
| `funding-event-reversal-long_only-lookback-90-threshold-1p5-hold-12x4h` | 47.56% | -458.90% | -458.90% | -49.22% | -297.90% |
| `funding-event-reversal-long_only-lookback-30-threshold-1-hold-4x4h` | -20.10% | -466.66% | -466.66% | -116.88% | -305.66% |
| `funding-event-reversal-long_only-lookback-180-threshold-2-hold-8x4h` | 139.74% | -475.77% | -475.77% | 42.96% | -314.77% |
| `funding-event-reversal-long_only-lookback-90-threshold-1-hold-8x4h` | 5.88% | -479.31% | -479.31% | -90.90% | -318.31% |
| `funding-event-reversal-long_only-lookback-180-threshold-1-hold-4x4h` | 88.06% | -479.65% | -479.65% | -8.72% | -318.65% |
| `funding-event-reversal-long_only-lookback-180-threshold-2-hold-12x4h` | 168.47% | -488.76% | -488.76% | 71.69% | -327.76% |
| `funding-event-reversal-long_only-lookback-30-threshold-1p5-hold-12x4h` | 25.71% | -492.64% | -492.64% | -71.06% | -331.64% |
| `funding-event-reversal-long_only-lookback-180-threshold-1p5-hold-12x4h` | 32.37% | -495.46% | -495.46% | -64.41% | -334.46% |
| `funding-event-reversal-long_only-lookback-90-threshold-2-hold-12x4h` | 25.49% | -499.40% | -499.40% | -71.29% | -338.40% |
| `funding-event-continuation-long_only-lookback-180-threshold-1-hold-8x4h` | -43.76% | -503.42% | -503.42% | -140.54% | -342.42% |
| `funding-event-reversal-long_only-lookback-180-threshold-1p5-hold-8x4h` | 33.73% | -508.04% | -508.04% | -63.05% | -347.04% |
| `funding-event-reversal-long_only-lookback-30-threshold-2-hold-12x4h` | 39.03% | -513.59% | -513.59% | -57.75% | -352.59% |
| `funding-event-reversal-long_only-lookback-90-threshold-1-hold-4x4h` | -15.72% | -518.62% | -518.62% | -112.50% | -357.62% |
| `funding-event-reversal-long_only-lookback-90-threshold-2-hold-8x4h` | 26.59% | -523.28% | -523.28% | -70.19% | -362.28% |
| `funding-event-reversal-long_only-lookback-30-threshold-1p5-hold-4x4h` | -47.06% | -526.19% | -526.19% | -143.84% | -365.19% |

Markdown 仅显示按开发期最差表现排序的前 20 个；完整候选见 `results.json`。
开发期合格成员：0 / 150。
只有开发期同时超过连续 1.5X BTC、无强平且盘中杠杆不超过 3X 的成员才读取 OOS。
