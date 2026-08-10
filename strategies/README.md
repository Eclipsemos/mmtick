# 策略库

本目录是 `research/soxl-history-backtest` 分支的策略权威索引。研究分支只维护历史数据、
回测证据和候选策略，不运行交易，也不向交易所提交订单。

## 目录

| 策略/部署 | 用途 | 状态 | 文档 |
|---|---|---|---|
| SOXL ATR32x3 仅做多 | 当前完整历史研究基线 | 冻结研究基线 | [current_research_baseline.md](current_research_baseline.md) |
| `soxl_perp_long` | `origin/main` long-only paper | 主分支部署快照 | [origin_main/soxl_perp_long.md](origin_main/soxl_perp_long.md) |
| `soxl_perp` | `origin/main` long/short paper 对照 | 主分支部署快照 | [origin_main/soxl_perp.md](origin_main/soxl_perp.md) |
| `soxl_perp_live` | `origin/main` Binance Futures 实盘 | 主分支部署快照 | [origin_main/soxl_perp_live.md](origin_main/soxl_perp_live.md) |

主分支部署快照的总览和共同执行语义见
[origin_main/README.md](origin_main/README.md)。这些文件记录 `origin/main@3c2253f` 的代码与
配置意图，不代表交易服务、账户门禁或持仓在任意时刻的实际状态。

## 维护规则

- 策略文档必须写明方向、周期、ATR、过滤器、动作锁、扩展退出、风险预算、初始化和成交模型。
- 部署策略以对应提交的配置和执行代码为证据；不要仅根据 Dashboard 文案推断。
- 研究候选必须链接可复现报告，并明确数据范围、成本、留出集和限制。
- 从主分支同步策略时新增或更新带提交号的部署快照，不把生产开关复制到研究配置。
- `reports/` 保存实验结果，`strategies/` 保存策略定义和批准状态；两者冲突时以本目录为准。
- 不在本目录保存 API 凭证、数据库、订单、成交或其他账户数据。
