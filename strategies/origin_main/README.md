# origin/main 部署策略快照

本目录通过只读 Git 检查整理，来源为 2026-08-10 获取的 `origin/main@3c2253f`
（`fix: restore approved futures risk budget`）。主分支配置两个隔离 paper 账户和一个独立实盘
账户；旧 SOXLB paper 已从生产配置移除。

## 账户映射

| 账户 | 类型 | 策略 | 方向 | 风险预算 | 扩展退出 |
|---|---|---|---|---:|---|
| `soxl_perp_long` | Paper Futures | ATR(32) × 3.0 | 仅做多 | 2x × 62.5% = 1.25x | 关闭 |
| `soxl_perp` | Paper Futures | ATR(21) × 4.0 | 多空 | 2x × 62.5% = 1.25x | 2.0/0.5 ATR 盈利保护 |
| `soxl_perp_live` | Binance USD-M Futures | ATR(32) × 3.0 | 仅做多 | 2x × 62.5% = 1.25x | 关闭 |

三者使用 `SOXLUSDT` Futures 行情，15 分钟 K 线、200 根预热、逐 Tick 信号检测、8 根 K 线
趋势效率和单 K 线一次动作锁。它们不共享策略状态或账本。

## 关键差异

- `soxl_perp_long` 和实盘共享信号参数，但初始化与成交模型不同。新 paper 账户允许一次启动
  趋势对齐；实盘代码强制标记启动对齐已检查，只等待新的有效上穿。
- `soxl_perp` 从全局 `[strategy]` 继承 ATR(21) × 4.0、效率 8/0.25 和 0.25 ATR 反向确认，
  同时由 instrument 配置启用 2.0/0.5 ATR 盈利保护。
- Paper 信号在下一笔持久化 Tick 按固定成本模拟成交；实盘以 Binance 实际订单和成交为准，
  并受凭证、激活确认、IP 白名单、账户模式、对账和滑点等门禁约束。

## 证据边界

本快照依据以下只读对象：

- `origin/main:config/settings.toml`
- `origin/main:src/mastermind_tick/config.py`
- `origin/main:src/mastermind_tick/engine.py`
- `origin/main:src/mastermind_tick/live_futures.py`
- `origin/main:README.md`
- `origin/main:changes.md`

Git 配置只能说明该提交准备运行什么，不能证明远程进程当前在线、门禁为 `ARMED`、账户空仓，
或生产数据库已经按同一提交完成重建。
