# mastermind:tick

`mastermind:tick` 是一个以 Binance 公共行情驱动的 SOXL 短周期交易系统，包含两个隔离的
paper 账户、一条独立的 Binance USDⓈ-M Futures 实盘链路、Tick 级回放工具和 React
监控台。系统持续保存行情、K 线、策略状态、订单、成交、资金费、现金流和账户绩效。

> **研究分支安全边界**：本分支只维护历史数据、回测策略和生产候选参数，不执行交易。当前
> 配置已关闭 `live_futures.enabled` 和 `live_futures.allow_order_submission`；不要在本分支
> 启动交易服务或提交 Binance 订单。

## 账户与产品

| 账户 ID | 产品 | 方向 | 初始资金/来源 | 目标敞口 |
|---|---|---|---|---:|
| `soxl_perp` | `SOXL/USDT PERP` paper | 仅做多 | 100,000 USDT | 1.25x |
| `soxl_perp_long` | 同一 Futures 行情的独立 paper 账户 | 仅做多 | 100,000 USDT | 1.25x |
| `soxl_perp_live` | Binance `SOXLUSDT` USD-M Futures | 仅做多 | Binance 实际余额 | 1.25x |

Paper 账户写入 `data/paper.db`，实盘账户写入 `data/live_futures.db`；真实余额、订单与成交
不会混入模拟账本。BTCUSDT 与 ETHUSDT 仅作为 research-only 回测品种，不会加入 paper 或
live 账户，也不会被交易引擎启动。

各品种历史原始压缩包分别保存在被 Git 忽略的 `data/history_soxl/`、`data/history_btc/` 与
`data/history_eth/`，导入后的 Tick、K 线和资金费率按 `instrument_id` 隔离写入
`data/paper.db`。运行 `scripts/maintenance/import_history.py` 做增量更新时，原始归档也会写入对应
目录，不再使用临时目录。

回测台内置三套品种预设：SOXL 使用已有研究候选网格；BTC 与 ETH 使用相同的中性研究基线
（多空、ATR 周期 `14/21/28`、倍数 `2/2.5/3`、`1x` 敞口）。BTC/ETH 预设尚未做历史优化，
其参数不是最优结论。首次点击“更新完整日”会从预设起点下载 Binance 日/月归档，之后从
数据库的最新 Tick 继续补齐缺失的官方日归档。

永续账户使用 `2x isolated` 和 62.5% 仓位预算：

```text
目标名义仓位 = 账户净值 × position_fraction × leverage
             = 账户净值 × 0.625 × 2
             = 账户净值 × 1.25
```

提高 `leverage` 而不降低 `position_fraction` 会同时放大收益、亏损、手续费和资金费。

## 当前策略

运行参数以 [config/settings.toml](config/settings.toml) 为准：

```text
方向                     仅做多
K 线周期                 15 分钟
基础退出                 Wilder ATR(32) × 3.0 递归跟踪线
趋势过滤                 8 根 K 线效率 >= 0.25
利润保护                 关闭
实盘延续重入             关闭
信号检测                 每个 Binance 成交 Tick
动作频率                 每根 K 线最多一个交易动作
```

完整研究基线和主分支部署策略快照见 [strategies/](strategies/README.md)。
维护、研究和报告脚本的职责与入口见 [scripts/README.md](scripts/README.md)。

### 基础信号与退出

价格从 ATR 跟踪线下方穿到上方时产生多头开仓信号；持有多仓时，从上方穿到下方会发送
`reduce_only` 平仓信号。新开仓必须通过趋势效率过滤，过滤器不会阻止已有仓位减仓。平仓后
保持空仓，等待下一次有效向上穿越，不建立空仓或自动反手。

Paper 账户首次启动时会执行一次趋势对齐。实盘首次启动不会按当前趋势追单，只等待新的有效
穿越；兼容策略状态会持久化，普通服务重启不会重复执行启动入场。

### 已关闭的扩展退出

ATR 盈利保护和延续重入实现仍保留在代码中，但当前配置均为 `0`，不会参与 paper 或实盘信号。
完整历史验证显示，旧版 `2.0 / 0.5 ATR` 盈利保护会过早截断盈利交易，`1.4 ATR` 重入也没有
独立的稳健优势。

## 行情、成交与记账

- Futures 使用 `SOXLUSDT` Trade 流并按 250 ms 聚合，保留底层 Trade ID 范围。
- Spot/Futures 均使用官方 15 分钟 K 线预热和定稿；收盘后通过 REST `/klines` 再校验。
- `soxl_perp`、`soxl_perp_long` 和实盘策略复用同一份 Futures 市场数据，但策略状态和账本
  相互独立。
- Paper 信号在下一笔持久化 Tick 按固定手续费与滑点成交；实盘只采用 Binance 返回的真实
  订单和成交结果。
- Paper Spot 默认手续费/滑点为 10/5 bps；Paper Futures 为 5/2 bps。实盘使用真实费用，
  开仓盘口偏离上限为 30 bps。
- 实盘订单使用确定性 `clientOrderId`。网络结果不明确时按该 ID 对账，不直接重复提交。
- Binance Futures `TRANSFER` 会幂等写入现金流；外部入金和出金不计入策略收益。
- 胜率按完整开仓—平仓轮次计算，分段成交归入同一轮；净值包含当前未实现盈亏。

## 安装与启动

需要 Python 3.11+、Node.js 20+ 和 npm。

首次安装依赖并构建前端：

```bash
cd /home/spaceaic/mmtick
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

cd frontend
npm ci
npm run build
cd ..
```

之后在项目根目录使用一键脚本后台启动回测平台：

```bash
cd /home/spaceaic/mmtick
./scripts/run.sh --host 0.0.0.0 --port 8100
```

`scripts/run.sh` 会自动使用项目的 `.venv` 和 `src/` 代码，同时提供 API 与
`frontend/dist/` 中的生产前端。启动后，本机访问 `http://127.0.0.1:8100`；其他计算机使用
运行机器的局域网 IP，例如 `http://10.162.133.214:8100`。脚本默认以 detached 模式运行，
PID 与日志分别保存在 `data/run/research.pid` 和 `data/run/research.log`。

查看状态、日志和停止服务：

```bash
./scripts/run.sh --status
tail -f data/run/research.log
./scripts/run.sh --stop
```

需要在终端前台运行时（例如本地调试或 Playwright 测试），增加 `--foreground`：

```bash
./scripts/run.sh --foreground --host 127.0.0.1 --port 8100
```

修改前端源码后，先重新构建再启动：

```bash
cd /home/spaceaic/mmtick/frontend
npm run build
cd ..
./scripts/run.sh --host 0.0.0.0 --port 8100
```

前端开发模式：

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

原生产交易链路保留在代码中供主分支维护；本研究分支不会启用真实订单。原交易门禁要求：

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

## 操作控制（主分支功能，本研究分支不启用）

- Paper Dashboard 支持暂停与恢复模拟成交，不停止行情和指标更新。
- LIVE 的“停止策略”会持久阻止后续策略订单，重启不会自动恢复；当前公开 API 不提供远程
  恢复操作。
- LIVE 的“平仓”要求二次确认，会重新读取 Binance 实际仓位并只发送减仓市价单；存在人工
  挂单或本地未决订单时拒绝执行。人工平仓不会自动停止策略。
- ATR 退出和可选利润保护是服务收到实时 Tick 后提交的市价减仓，不是预挂在 Binance 的原生
  止损单。服务、网络或行情中断期间无法触发。

## Dashboard 与 API

Dashboard 提供 PAPER/LIVE 切换、价格与官方 K 线、ATR 与利润保护线、交易标记、持仓估值、
保证金、资金费、订单、完整轮次胜率、收益周期、仓库覆盖和 CSV 导出。LIVE 参数默认隐藏，
敏感账户数据需要操作员会话。

主要接口：

```text
GET  /api/health
GET  /api/overview
POST /api/control                 {"action":"pause" | "resume"}
GET  /api/accounts/{id}/equity
GET  /api/accounts/{id}/returns
GET  /api/orders | /api/fills | /api/events | /api/funding
GET  /api/warehouse
GET  /api/market/ohlcv | /api/market/agg-trades

GET  /api/live/readiness          # 公开健康信息
GET  /api/live/session
POST /api/live/unlock | /api/live/unlock-local | /api/live/logout
GET  /api/live/overview | /api/live/equity | /api/live/returns
GET  /api/live/orders | /api/live/fills | /api/live/funding | /api/live/events
POST /api/live/control            {"action":"stop"}
POST /api/live/flatten            {"confirm":"FLATTEN_SOXLUSDT"}
```

## 回放与 paper 重建

使用已持久化的 `agg_trades` 做 Tick 级 ATR 参数回放：

```bash
PYTHONPATH=src .venv/bin/python -m mastermind_tick.backtest \
  --instrument soxl_perp \
  --periods 32 \
  --multipliers 3.0 \
  --output-dir reports/experiments/validation
```

回放包含 Tick 穿越、趋势过滤、动作锁、下一 Tick 成交、手续费、滑点、杠杆和历史资金费，
但不模拟盘口深度、真实 API 延迟或强制清算。可选利润保护对比入口：

```bash
PYTHONPATH=src .venv/bin/python -m mastermind_tick.profit_backtest
```

Paper 账户重建默认先生成候选数据库，不直接替换生产派生账本：

```bash
.venv/bin/mmtick-rebuild --account-id soxl_perp \
  --candidate data/rebuild-soxl-perp.db
```

`--apply` 会创建可恢复备份后替换所选账户，属于生产数据变更；执行前应停止相关写入并检查候选
报告、市场数据只读校验和账户范围。

研究报告按结论、优化、月度、重评和归档分类，入口见 [reports/README.md](reports/README.md)；
系统与策略变更记录见 [changes.md](changes.md)。回测收益只说明
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
- 实盘 ATR 退出与可选利润保护不是交易所托管止损，运行进程和行情连接是执行依赖。
- 当前开仓信号在 `SLIPPAGE_LIMIT` 拦截后不会自动重试，并可能已消费本 K 线动作机会；该交易
  运行问题不属于本研究分支范围。减仓信号不受滑点阈值限制，但当前仍会执行盘口查询。
- 当前工程只读取并校验交易所杠杆和保证金模式，尚未封装修改杠杆或增减逐仓保证金的操作 API。
- 参数变更、paper 重建和实盘切换都需要明确的样本外验证与可恢复部署流程。
