# BTC 分钟级高频策略探索

Decision: `rejected`. Paper/live approval: `false/false`.

本实验使用真实 BTCUSDT 永续 aggTrades 聚合 1m/5m 成交流，但没有历史盘口、
队列位置或订单延迟，因此属于分钟级微观结构研究，不是可证明的低延迟 HFT。

## 数据与协议

- 1m bars: `1,372,286`；5m bars: `274,455`。
- 实际买卖方字段覆盖名义金额：`22.11%`；tick-rule代理覆盖：`84.82%`。
- 网格候选：`576`。2024训练、2025验证，2026-01-01至08-10仅复用确认。
- 信号在闭合bar计算，最早下一根bar收盘成交；固定1x、不重叠持仓。
- 成本：零成本；乐观maker 2+0.5 bps；基础taker 5+2 bps；压力taker 10+5 bps，均为每次fill。

## 开发期筛选

| 成本 | 2024/2025均盈利候选 | 入选参数 | 2024 | 2025 | 交易数(2024/2025) |
|---|---:|---|---:|---:|---:|
| zero | 125 | `reported_flow_revert-1m-window360-threshold1p5-hold10` | 211.49% | 322.01% | 29,624/29,212 |
| optimistic_maker | 0 | `reported_flow_follow-1m-window120-threshold2p5-hold1` | -13.87% | -16.60% | 342/337 |
| base_taker | 0 | `reported_flow_follow-1m-window120-threshold2p5-hold1` | -36.70% | -38.44% | 342/337 |

## 复用确认成本敏感性

下表分别冻结各成本口径在2024/2025选出的候选，再查看2026；不是用2026重新选参。

| 开发选择口径 | 候选 | 零成本 | 乐观maker | 基础taker | 压力taker | DD(base) | 单fill盈亏平衡成本 |
|---|---|---:|---:|---:|---:|---:|---:|
| zero | `reported_flow_revert-1m-window360-threshold1p5-hold10` | 24.64% | -99.98% | -100.00% | -100.00% | -100.00% | 0.07 bps |
| optimistic_maker | `reported_flow_follow-1m-window120-threshold2p5-hold1` | -0.37% | -24.97% | -54.98% | -81.87% | -54.98% | -0.03 bps |
| base_taker | `reported_flow_follow-1m-window120-threshold2p5-hold1` | -0.37% | -24.97% | -54.98% | -81.87% | -54.98% | -0.03 bps |

## 基础taker候选月收益

Candidate: `reported_flow_follow-1m-window120-threshold2p5-hold1`.

| 月份 | 收益 |
|---|---:|
| 2026-01 | -6.07% |
| 2026-02 | -19.27% |
| 2026-03 | -14.48% |
| 2026-04 | -10.36% |
| 2026-05 | -4.35% |
| 2026-06 | -11.60% |
| 2026-07 | -7.41% |
| 2026-08 | -1.09% |

## 解释

高频策略的核心问题不是能否找到零成本预测，而是每次交易的毛优势能否覆盖两次
成交成本。`单fill盈亏平衡成本`应与maker/taker实际总成本直接比较。即使乐观maker
回放为正，没有盘口和队列数据也无法证明会成交，更无法量化被动成交后的逆向选择。

最终结论：No development-robust strategy survived both base and stress taker costs. 本实验不批准模拟盘或实盘。

## 限制

- 2026 is reused confirmation evidence, not a fresh holdout.
- No historical bid/ask, queue position, depth, cancel latency, or maker fill probability.
- Funding is not charged in this sub-hour replay; longer-hold results must add it.
- Tick-rule direction is a 250ms bucket proxy and is not exchange-reported aggressor side.
- The bare-price SQLite aggregate uses the trade at the minute's maximum timestamp; ties are arbitrary.
- Fixed slippage cannot reproduce spread widening, market impact, or adverse selection.

机器可读结果见 [`results.json`](results.json)。
