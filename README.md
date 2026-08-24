# mastermind:tick

`mastermind:tick` 是一个以 Binance 公共行情驱动的量化交易系统，包含 SOXL ATR paper、
BTC/ETH 组合策略 paper、一条独立的 Binance USDⓈ-M Futures 实盘链路、Tick 级回放工具和 React
监控台。系统持续保存行情、K 线、策略状态、订单、成交、资金费、现金流和账户绩效。

> **实盘提示**：仓库当前配置已启用 `SOXLUSDT` 实盘运行时和真实订单开关。只有凭证、启动
> 确认、IP 白名单、账户模式、测试订单和对账等全部门禁通过时才会进入 `ARMED`。开发环境应
> 先将 `live_futures.allow_order_submission` 改为 `false`。

## 账户与产品

| 账户 ID | 产品 | 方向 | 初始资金/来源 | 目标敞口 |
|---|---|---|---|---:|
| `soxl_perp_long` | `SOXL/USDT PERP LONG ONLY` paper | 仅做多 | 100,000 USDT | 1.25x |
| `soxl_perp_long_session_reentry` | `SOXL/USDT PERP LONG ONLY + SESSION REENTRY` paper | 仅做多 | 100,000 USDT | 1.25x |
| `btc_eth_calendar_router` | BTC/ETH 扩展月历路由 paper | 组合多空 | 100,000 USDT | 4x 外层模型 |
| `soxl_perp_live` | Binance `SOXLUSDT` USD-M Futures | 仅做多 | Binance 实际余额 | 1.25x |

Paper 账户写入 `data/paper.db`，实盘账户写入 `data/live_futures.db`；真实余额、订单与成交
不会混入模拟账本。旧 `soxl_perp` 多空模拟账本已经删除，但该 ID 保留为 SOXL LONG ONLY
与实盘共用的行情源；它不会再作为模拟账户运行或展示。

实盘和 long-only paper 使用 `2x isolated` 和 62.5% 仓位预算：

```text
目标名义仓位 = 账户净值 × position_fraction × leverage
             = 账户净值 × 0.625 × 2
             = 账户净值 × 1.25
```

提高 `leverage` 而不降低 `position_fraction` 会同时放大收益、亏损、手续费和资金费。

## 当前策略

运行参数以 [config/settings.toml](config/settings.toml) 为准：

```text
策略名称                 soxl_long_atr32x3_v1
K 线周期                 15 分钟
实盘基础止损             Wilder ATR(32) × 3.0 递归跟踪线
趋势过滤                 8 根 K 线效率 >= 0.25
实盘交易方向             仅做多
实盘利润保护             关闭
实盘延续重入             关闭
信号检测                 每个 Binance 成交 Tick
动作频率                 每根 K 线最多一个交易动作
```

### 基础信号与反向

实盘和 `soxl_perp_long` 在价格从 ATR 跟踪线下方穿到上方且趋势效率过滤通过时开多；持有
多仓时，下穿 ATR 跟踪线会发送 `reduce_only` 平仓。平仓后保持空仓，等待下一次有效上穿，
不开空、不反手、
不执行延续重入。`soxl_perp_long` 使用与实盘相同的 ATR(32) × 3.0、62.5% 仓位策略。
独立的 `soxl_perp_long_session_reentry` 从同一策略起点建账，只在北京时间周日/周一 08:00、
周二至周四 16:00 或每日 21:30 的 ±30 分钟 ATR 退出后观察恢复重入；它不会改变实盘。
完整规则见
[strategies/paper/soxl_atr32x3_session_reentry_v1.md](strategies/paper/soxl_atr32x3_session_reentry_v1.md)。

BTC/ETH paper 使用冻结的月历路由：状态袖套占 50%，每月固定三个 MACD 趋势袖套各占
1/6，组合应用 4x 外层研究杠杆与 -20%/+18% UTC 月度锁。基础与压力成本账本并行保存，
只从 2026-08-16 UTC 起记录前向收益，不允许回写。

该 ATR(32) × 3.0 策略的历史胜率为 `42.28%`，依赖少数大赢家，不属于高胜率目标策略。
`2x × 70% = 1.40x` 因完整样本回撤 `-29.42%`、新增尾段亏损 `-2.76%` 且回放未模拟强平而
未获批准；当前维持 1.25x 研究基线，高胜率策略研究暂缓。

Paper 账户首次启动时会执行一次趋势对齐。实盘首次启动不会按当前趋势追单，只等待新的有效
穿越；兼容策略状态会持久化，普通服务重启不会重复执行启动入场。

完整实盘策略和风险预算研究见
[strategies/live/soxl_atr32x3_long_v1.md](strategies/live/soxl_atr32x3_long_v1.md)。

## 行情、成交与记账

- Futures 使用 `SOXLUSDT` Trade 流并按 250 ms 聚合，保留底层 Trade ID 范围。
- 系统使用官方 15 分钟 Futures K 线预热和定稿；收盘后通过 REST `/klines` 再校验。
- 两个 SOXL long-only paper 和实盘策略复用 `soxl_perp` Futures 市场数据；共享行情不等于
  共享账本。
- BTC/ETH 路由只处理完整 UTC 日线和 4h K 线，收盘产生信号、下一根日线开盘成交，并将
  各袖套目标、成本、基础/压力收益和月度锁写入独立日账本。
- Paper 信号在下一笔持久化 Tick 按固定手续费与滑点成交；实盘只采用 Binance 返回的真实
  订单和成交结果。
- Paper Futures 使用 5/2 bps 手续费/滑点。实盘使用真实费用，开仓盘口偏离上限为 30 bps。
- 实盘订单使用确定性 `clientOrderId`。网络结果不明确时按该 ID 对账，不直接重复提交。
- Binance Futures `TRANSFER` 会幂等写入现金流；外部入金和出金不计入策略收益。
- 胜率按完整开仓—平仓轮次计算，分段成交归入同一轮；净值包含当前未实现盈亏。

## 安装与启动

需要 Python 3.11+、Node.js 20+ 和 npm。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

cd frontend
npm ci
npm run build
cd ..

./scripts/run.sh --host 127.0.0.1 --port 8100
```

Dashboard 默认地址为 `http://127.0.0.1:8100`。前端开发模式：

```bash
cd frontend
npm run dev
```

Vite 会将 `/api` 代理到 `127.0.0.1:8100`。若已安装用户级服务，可使用：

```bash
systemctl --user status mmtick.service
journalctl --user -u mmtick.service -f
```

停止服务不会自动平仓。远程访问应让应用监听 loopback，并通过设置正确
`X-Forwarded-Proto` 的 HTTPS 反向代理暴露；不要直接将实盘控制面板暴露在明文公网。

## 配置与实盘门禁

主配置为 [config/settings.toml](config/settings.toml)，也可通过 `MMTICK_CONFIG` 指定另一份
文件。API 凭证从被 Git 忽略的 `.env` 读取，当前键名为 `API_KEY` 和 `SECRET_KEY`；操作员
令牌位于 `data/operator.token`。两者权限必须为 `600`，不得写入仓库、日志、聊天或 URL。

真实订单至少要求：

```text
live_futures.enabled = true
live_futures.allow_order_submission = true
MMTICK_LIVE_CONFIRM = SOXLUSDT_PERP_LIVE   # 进程环境变量，不是普通配置项
```

运行时还会校验：

- API 读取和 Futures 交易权限开启、提现关闭、IP 白名单开启；
- Single-Asset、Hedge Mode、`SOXLUSDT` 为目标 `2x isolated`；
- Futures test order 已通过并写入实盘库（TradFi-Perps 协议未接受时该测试无法通过）；
- 无未知挂单、其他合约持仓、同时多空腿、未接管仓位或对账错误；
- 策略未被操作员持久停止。

预检与无成交测试：

```bash
.venv/bin/mmtick-live-preflight
.venv/bin/mmtick-live-preflight --test-order
```

`--test-order` 调用 `/fapi/v1/order/test`，不会创建订单。只有账户所有者已经阅读并明确同意
Binance TradFi-Perps 协议时，才可执行：

```bash
.venv/bin/mmtick-live-preflight --sign-tradfi-contract --test-order
```

该命令会通过 `/fapi/v1/stock/contract` 改变账户协议状态。

公开的 `/api/live/readiness` 仅返回门禁和健康信息。余额、仓位、订单、成交和收益接口需要
操作员会话：本机 loopback 可调用 `/api/live/unlock-local`，远程浏览器需使用操作员令牌登录。
会话使用 HttpOnly、SameSite=Strict Cookie，有效期 8 小时；HTTPS 代理下同时启用 Secure
Cookie 和 HSTS。

## 操作控制

- Paper Dashboard 支持暂停与恢复模拟成交，不停止行情和指标更新。
- LIVE 的“停止策略”会持久阻止后续策略订单，重启不会自动恢复；已认证的操作员可在 Dashboard
  二次确认后重新启动。启动会先刷新 Binance 对账并验证全部交易门禁，失败时保持停止。
- LIVE 的“平仓”要求二次确认，会重新读取 Binance 实际仓位并只发送减仓市价单；存在人工
  挂单或本地未决订单时拒绝执行。人工平仓不会自动停止策略。
- 实盘 ATR 止损是服务收到实时 Tick 后提交的市价减仓，不是预挂在 Binance 的原生止损单。
  服务、网络或行情中断期间无法触发。

## Dashboard 与 API

Dashboard 提供 PAPER/LIVE 切换、价格与官方 K 线、ATR 与利润保护线、交易标记、持仓估值、
保证金、资金费、订单、完整轮次胜率、收益周期、仓库覆盖和 CSV 导出。LIVE 参数默认隐藏，
敏感账户数据需要操作员会话。价格图中的 `LONG`、`SHORT`、`CLOSE` 标记与时间轴 tooltip
相互隔离：鼠标可在包含成交标记的区间连续查看任意采样时刻的市场价格和 ATR；直接悬停标记
时才附加显示对应成交动作，移开后恢复普通价格查看。

主要接口：

```text
GET  /api/health
GET  /api/overview
POST /api/control                 {"action":"pause" | "resume"}
GET  /api/accounts/{id}/equity
GET  /api/accounts/{id}/returns
GET  /api/accounts/{id}/portfolio-ledger?ledger=base|stress
GET  /api/orders | /api/fills | /api/events | /api/funding
GET  /api/warehouse
GET  /api/market/ohlcv | /api/market/agg-trades

GET  /api/live/readiness          # 公开健康信息
GET  /api/live/session
POST /api/live/unlock | /api/live/unlock-local | /api/live/logout
GET  /api/live/overview | /api/live/equity | /api/live/returns
GET  /api/live/orders | /api/live/fills | /api/live/funding | /api/live/events
POST /api/live/control            {"action":"stop"}
POST /api/live/control            {"action":"resume","confirm":"RESUME_SOXLUSDT"}
POST /api/live/flatten            {"confirm":"FLATTEN_SOXLUSDT"}
```

## 回放与 paper 重建

完整 SOXLUSDT 研究数据使用 Binance 官方 USD-M 月度/日度 `aggTrades` 归档，并用最近两天
REST 数据衔接。下载文件会校验官方 SHA-256，逐档验证 aggregate trade ID 连续性，然后按生产
行情相同的 250 ms 粒度写入独立研究库；不会改动 paper 或实盘账本：

```bash
PYTHONPATH=src .venv/bin/python -m mastermind_tick.historical_data \
  --database data/soxlusdt_history.db
```

使用已持久化的 `agg_trades` 做 Tick 级 ATR 参数回放：

```bash
PYTHONPATH=src .venv/bin/python -m mastermind_tick.backtest \
  --instrument soxl_perp \
  --periods 21 \
  --multipliers 4.0 \
  --output-dir reports/validation
```

回放包含 Tick 穿越、趋势过滤、分阶段反向、动作锁、下一 Tick 成交、手续费、滑点、杠杆和
历史资金费，但不模拟盘口深度、真实 API 延迟或强制清算。利润保护对比入口：

```bash
PYTHONPATH=src .venv/bin/python -m mastermind_tick.profit_backtest
```

Paper 账户重建默认先生成候选数据库，不直接替换生产派生账本：

```bash
.venv/bin/mmtick-rebuild --account-id soxl_perp_long \
  --candidate data/rebuild-soxl-perp-long.db
```

若账户需要从策略正式切换时间重新以初始资金计算绩效，可增加 UTC epoch 毫秒截点；截点前行情
仍保留并用于 ATR 预热，但不会生成该账户的订单、成交、快照或收益：

```bash
.venv/bin/mmtick-rebuild --account-id soxl_perp_long \
  --start-ms 1785945639000 \
  --candidate data/rebuild-soxl-perp-long-since-cutover.db
```

`--apply` 会创建可恢复备份后替换所选账户，属于生产数据变更；执行前应停止相关写入并检查候选
报告、市场数据只读校验和账户范围。

研究报告保存在 `reports/`，系统与策略变更记录见 [changes.md](changes.md)。回测收益只说明
给定数据、成本和撮合模型下的历史结果，不代表未来表现。

## 开发与验证

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests

cd frontend
npm run build
npm run test:e2e            # 需要已运行的 127.0.0.1:8100 服务
```

Python 源码位于 `src/mastermind_tick/`，测试位于 `tests/`；React/TypeScript 前端位于
`frontend/src/`，Playwright 测试位于 `frontend/tests/`。贡献规范见 [AGENTS.md](AGENTS.md)。

## 已知限制

- 回测样本仍短，部分参数来自同一数据区间优化；高胜率不能替代对平均盈亏、成本和回撤的检查。
- Paper 撮合未完整模拟盘口深度、排队、拒单、网络延迟、强平和跳空。
- 实盘 ATR 与利润保护不是交易所托管止损，运行进程和行情连接是执行依赖。
- 当前开仓信号在 `SLIPPAGE_LIMIT` 拦截后不会自动重试，并可能已消费本 K 线动作机会；修复计划
  见 [TODO.md](TODO.md)。减仓信号不受滑点阈值限制，但当前仍会执行盘口查询。
- 当前工程只读取并校验交易所杠杆和保证金模式，尚未封装修改杠杆或增减逐仓保证金的操作 API。
- 参数变更、paper 重建和实盘切换都需要明确的样本外验证与可恢复部署流程。
