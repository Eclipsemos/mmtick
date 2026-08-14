# 策略库

本目录是 `research/soxl-history-backtest` 分支的策略权威索引。研究分支只维护历史数据、
回测证据和候选策略，不运行交易，也不向交易所提交订单。

## 目录

| 策略/部署 | 用途 | 状态 | 文档 |
|---|---|---|---|
| SOXL ATR32x3 仅做多 | 当前完整历史研究基线 | 冻结研究基线 | [current_research_baseline.md](current_research_baseline.md) |
| SOXL True Range 波差 v1 | 冻结前向研究候选 | 证据不足，未批准 | [candidates/soxl_volatility_spread_true_range_v1.md](candidates/soxl_volatility_spread_true_range_v1.md) |
| `soxl_perp_long` | `origin/main` long-only paper | 主分支部署快照 | [origin_main/soxl_perp_long.md](origin_main/soxl_perp_long.md) |
| `soxl_perp` | `origin/main` long/short paper 对照 | 主分支部署快照 | [origin_main/soxl_perp.md](origin_main/soxl_perp.md) |
| `soxl_perp_live` | `origin/main` Binance Futures 实盘 | 主分支部署快照 | [origin_main/soxl_perp_live.md](origin_main/soxl_perp_live.md) |

## 四种环境对比

| 环境 | 用途/账户 | 方向 | 周期与 ATR | 趋势过滤 | 动作锁 | 盈利保护 | 反向/重入 | 风险预算 | 初始化与成交 |
|---|---|---|---|---|---|---|---|---|---|
| 研究基线 | 历史回测 | 仅做多 | 15m，ATR(32) × 3 | 8 / 0.25 | 固定 15m | 关闭 | 不开空；重入关闭 | 2x × 62.5% = 1.25x | 200 根 K 线预热；下一持久化 Tick，5/2 bps |
| Long-only paper | `soxl_perp_long` | 仅做多 | 15m，ATR(32) × 3 | 8 / 0.25 | 固定 15m | 关闭 | 不开空；重入关闭 | 2x × 62.5% = 1.25x | 200 根预热，可一次启动趋势对齐；下一持久化 Tick，5/2 bps |
| Long/short paper | `soxl_perp` | 多空 | 15m，ATR(21) × 4 | 8 / 0.25 | 固定 15m | 2.0 ATR 激活，0.5 ATR 跟踪 | 反向确认 0.25 ATR；重入关闭 | 2x × 62.5% = 1.25x | 200 根预热，可一次启动趋势对齐；下一持久化 Tick，5/2 bps |
| Live Futures | `soxl_perp_live` | 仅做多 | 15m，ATR(32) × 3 | 8 / 0.25 | 固定 15m | 关闭 | 不开空；重入关闭 | 2x isolated，62.5% 仓位 = 1.25x | 200 根预热并恢复状态；禁止启动追入；Binance 实际成交 |

四种环境共享 SOXLUSDT Futures 行情和 ATR 穿越逻辑，但不能把它们视为同一条运行路径：
研究基线用于回测，两个 paper 使用模拟撮合，实盘还受凭证、账户模式、对账、滑点和操作员门禁
约束。实盘配置中的启用开关也不等于当前服务一定处于可下单状态。

主分支部署快照的总览和共同执行语义见
[origin_main/README.md](origin_main/README.md)。这些文件记录 `origin/main@3c2253f` 的代码与
配置意图，不代表交易服务、账户门禁或持仓在任意时刻的实际状态。

## 维护规则

- 策略文档必须写明方向、周期、ATR、过滤器、动作锁、扩展退出、风险预算、初始化和成交模型。
- 部署策略以对应提交的配置和执行代码为证据；不要仅根据 Dashboard 文案推断。
- 研究候选必须链接可复现报告，并明确数据范围、成本、留出集和限制。
- 冻结候选使用机器可读参数文件；锁定日及以前的数据不得重新标记为前向证据。
- 从主分支同步策略时新增或更新带提交号的部署快照，不把生产开关复制到研究配置。
- `reports/` 保存实验结果，`strategies/` 保存策略定义和批准状态；两者冲突时以本目录为准。
- 不在本目录保存 API 凭证、数据库、订单、成交或其他账户数据。
