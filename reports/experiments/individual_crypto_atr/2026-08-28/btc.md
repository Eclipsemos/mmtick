# BTCUSDT 独立 ATR 策略初筛

生成时间：`2026-08-28T03:23:22.857334+00:00`

本研究只分析单一品种，不读取或修改冻结的 BTC/ETH 组合策略。当前阶段使用完整的 15 分钟 bar 做因果初筛；入选者才进入 2024 年至今 250 ms tick 精确回放。

## 数据与分割

- 数据：`2020-01-01T00:00:00+00:00` 至 `2026-08-25T18:44:59.999000+00:00`，233,163 根 15 分钟 bar。
- 开发：2020-2023；验证：2024；确认：2025；2026 年仅作诊断。
- `2026-08-22` 以后保留为 forward observation，不参与选择。
- 基准成本：每次成交 5 bps 手续费 + 2 bps 滑点；压力成本为 10 + 5 bps。

## 开发期选出的各家族代表

| 家族 | 候选 | 开发 | 验证 | 确认 | 2026诊断 | Forward | 确认DD | 确认PF | 通过 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| atr_mean_reversion | `atr-mean-1440m-20-14-1-long_only` | 28.04% | 21.36% | 37.57% | -31.58% | 0.00% | -20.30% | 3.37 | 否 |
| atr_trailing_stop | `atr-stop-240m-21-4-long_only` | 321.74% | 68.96% | -23.99% | 0.18% | 1.23% | -32.12% | 0.61 | 否 |
| chandelier_breakout | `chandelier-240m-20-14-4-long_only` | 297.63% | 93.79% | -8.92% | -1.28% | 1.23% | -22.43% | 0.80 | 否 |
| keltner_breakout | `keltner-240m-50-28-1.5-long_short` | 146.77% | 67.50% | -48.13% | 17.90% | 1.23% | -52.24% | 0.40 | 否 |

## 判定

状态：`no_stable_bar_candidate`。

通过全部基础门槛的家族：无。

The archive and ATR family set have been inspected before. Only observations recorded after this report can become pristine forward evidence.

Replay development-selected family winners on the independent 2024-present 250 ms tick warehouses; do not retune on forward observations.

本报告不批准模拟盘或实盘。
