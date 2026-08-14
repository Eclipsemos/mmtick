# SOXL 波差候选：True Range Compression Release v1

这是冻结的前向研究候选，不是交易授权。参数在已知数据截至 `2026-08-13 UTC` 后登记；只有
`2026-08-14 UTC` 及之后的完整自然日可以增加前向证据。

## 参数

| 项目 | 值 |
|---|---:|
| 波差定义 | 归一化 True Range 快慢比 |
| 形态 | 波动压缩后释放突破 |
| 方向 | 多空 |
| 快/慢窗口 | 12 / 64 根 15m K 线 |
| 入场/退出比值 | 1.1 / 0.8 |
| 突破窗口 | 24 根 |
| 压缩阈值/回看 | 0.85 / 16 根 |
| 跟踪停止距离 | 2.5 倍慢速平均 True Range |
| 最长持有 | 96 根 |
| 目标敞口 | 1.25x 权益 |
| 成本 | 每次成交 5 bps 手续费 + 2 bps 滑点，计资金费 |
| 成交 | 收盘 K 线产生信号，下一根 K 线第一笔持久化 Tick 成交 |

机器可读定义见
[soxl_volatility_spread_true_range_v1.json](soxl_volatility_spread_true_range_v1.json)。来源报告见
[Phase 2 results](../../reports/experiments/soxl_volatility_spread/2026-08-14-v2/README.md)。

## 已知证据与限制

截至 8 月 10 日，候选累计收益 `+180.23%`、几何日收益 `+1.21%`、Tick 路径最大回撤
`-13.59%`。8 月 11–13 日冻结留出段收益 `+0.74%`，仅 3 笔完成交易；局部参数稳定率
`62.5%`，低于研究门槛。开发样本的正向 PnL 中 `76.41%` 来自空头，前五笔盈利贡献
`54.79%`，说明收益仍依赖少量趋势行情。

把敞口提高到 10x 虽在开发样本达到 `+5.13%/日`，但 Tick 路径回撤达到 `-89.45%`，且
没有模拟强平；这不是可执行的 5% 方案。候选状态保持 `insufficient_fresh_evidence`，不得用于
生产或加杠杆。前向中期复评至少需要 30 个完整 UTC 日和 20 笔交易；批准性复评至少需要
90 日和 100 笔交易。

区块重采样进一步显示，10x 敞口只有约 `51.7%–54.1%` 的 90 日路径达到 5% 几何日收益，
但 `93.3%–95.7%` 的路径出现至少 -50% 日线回撤。详见
[5% 可行性报告](../../reports/experiments/soxl_volatility_spread/2026-08-14-v2/target_feasibility.md)。

更新完历史数据后，使用下列命令重复生成确定性的前向报告：

```bash
.venv/bin/python scripts/evaluate_soxl_volatility_spread_forward.py
```
