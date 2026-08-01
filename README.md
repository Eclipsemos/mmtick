# mastermind:tick

Mastermind 旗下短线策略产品。当前版本使用提交 `25784e3` 的原始 ATR Tick 算法，并行运行在两个独立模拟账户上，实时记录 Binance SOXLB Spot 与 SOXL TradFi Perpetual 的行情、信号、成交和绩效。

## 产品边界

- `SOXLB/USDT`：Binance 上的 SOXL bStocks 代币化产品，使用 Binance 公共聚合成交实时驱动。
- `SOXL/USDT PERP`：Binance USDⓈ-M `TRADIFI_PERPETUAL`，使用公开 Futures 行情实时驱动。
- `soxlb` 与 `soxl_perp` 各有独立的 100,000 USDT 模拟账本、订单、持仓和绩效序列。
- 当前是本地 paper broker，不提交真实 Binance 或券商订单。
- Binance Testnet/Demo 没有这两个标的，系统使用生产公共行情和本地模拟成交。

## 策略与成交

默认配置来自 [strategy_v1.md](strategy_v1.md)：

```text
15 分钟 K 线
ATR Period = 7
ATR Multiplier = 1.0
SOXLB 现货仅做多；SOXL 永续以 2x 多空反手
成交 Tick 实时上穿后目标多头，实时下穿后目标空头/现货平仓
信号在当前 Tick 生成，并在下一成交 Tick 模拟成交
```

ATR 使用 TradingView `ta.atr` 对应的 Wilder RMA。启动时使用 Binance 官方历史 K 线预热；运行后由收到的成交 Tick 合成 15 分钟 K 线。每个 Tick 使用更新前的价格和 ATR 止损线判断穿越，随后递归更新当前 ATR 与止损线，保持 `25784e3` 的原始执行顺序。

价格从 ATR 线下方实时穿到上方时触发 BUY，从上方实时穿到下方时触发 SELL。SOXLB 的 SELL 平掉多头；SOXL Perpetual 的 SELL 平多并反手做空，BUY 平空并反手做多。系统不使用时间防抖；同一根 K 线的买入、卖出及卖出后重入仍受原策略锁控制。订单在信号后的下一成交 Tick 撮合。Binance 官方 15 分钟 K 线继续通过 REST `/klines` 校验并写入仓库和蜡烛图，但不会改写或暂停运行中的 Tick 策略。SOXLB 默认单边手续费 10 bps、滑点 5 bps。SOXL Perpetual 使用 2x 逐仓模型、95% 保证金利用率、5 bps taker 手续费和 2 bps 模拟滑点。参数位于 [config/settings.toml](config/settings.toml)。

永续账户按带方向持仓和标记价格计算未实现盈亏、初始保证金与可用余额。每 8 小时从 Binance 公共资金费历史同步实际费率：正费率时多头支付、空头收取，并计入现金、净值、交易胜率和收益区间。

## 数据

- 实时 SOXLB 成交：`wss://data-stream.binance.vision/ws/soxlbusdt@aggTrade`；官方收盘 K 线：`@kline_15m`，每根收盘后用 market-data-only REST 校验。
- 实时 SOXL Perpetual 成交：`wss://fstream.binance.com/ws/soxlusdt@trade`；原始成交在 feed 内按 250 ms 聚合，保留数量、名义金额、最高/最低价和底层 Trade ID 范围。
- SOXL Perpetual 官方收盘 K 线：`@kline_15m` + `https://fapi.binance.com/fapi/v1/klines` REST 校验；标记价、指数价和资金费来自 Futures 公共 REST。
- 模拟账本：`data/paper.db`，SQLite WAL 模式。
- `agg_trades`：持久化 Spot aggTrade 或 Futures 250 ms 成交批次、底层交易 ID、事件/成交时间、价格、数量、名义金额和 maker 方向。
- `ohlcv_bars`：保存 Binance 官方历史/收盘 15 分钟 OHLCV，以及由成交流临时形成中的当前 OHLCV；官方收盘值会覆盖临时值。
- `funding_payments`：保存永续账户每次资金费率、标记价格、持仓名义价值和收付金额。

行情表采用事件 ID 和 K 线起始时间幂等写入，重连或重启不会重复累计相同成交。仓库页面显示主库、WAL、分表及索引占用；当前未配置自动清理策略，aggTrade 将持续累积。

系统不再读取 SOXL 行情或 `~/mm` 数据。Alpha 的历史研究、真实基金账户与 `mastermind:tick` paper 绩效相互隔离。

## 启动

Python 环境使用 `/home/spaceaic/env/.venv`。前端生产文件已构建到 `frontend/dist`。

```bash
./scripts/run.sh --host 127.0.0.1 --port 8100
```

打开 `http://127.0.0.1:8100`。服务启动后会自动恢复已有账户和策略运行状态，并继续追加净值记录。

页面提供：

- SOXLB 与 SOXL Perpetual 账户切换、实时价格、ATR 移动止损线和 K 线收盘状态；
- 独立的 Binance 官方 15 分钟 K 线图，显示 OHLC、买卖成交标记、滚动、缩放和 REST 校验状态；
- Futures 标记价、指数价、资金费率、初始保证金、可用余额和累计资金费；
- 实时交易决策状态、Tick 信号门控、最近穿越结果及下一触发条件；
- 价格图上的买入/卖出图标、醒目的净交易百分比、区间滚动和缩放；
- 现金、持仓、累计收益、费用和最大回撤；
- 手续费后完整交易胜率和 15 分钟年化夏普率；
- 净值曲线、订单和成交；
- 收益明细：最近 30 个自然日收益日历、最近 12 周、最近 12 月及成立以来年化收益；
- 数据仓库的 OHLCV、aggTrade、覆盖时间、记录数和磁盘占用；
- 暂停/恢复信号执行和成交 CSV 导出。

## 开发与验证

```bash
export PYTHONPATH="$PWD/src"
/home/spaceaic/env/.venv/bin/pytest
/home/spaceaic/env/.venv/bin/ruff check src tests

cd frontend
npm run build
```

开发前端：

```bash
cd frontend
npm run dev
```

Vite 会把 `/api` 转发到 `127.0.0.1:8100`。

## API

```text
GET  /api/health
GET  /api/overview
GET  /api/accounts/{id}/equity
GET  /api/accounts/{id}/returns
GET  /api/orders
GET  /api/fills
GET  /api/events
GET  /api/funding
GET  /api/fills.csv
GET  /api/warehouse
GET  /api/market/ohlcv
GET  /api/market/agg-trades
POST /api/control       {"action":"pause" | "resume"}
```

收益明细按浏览器本地时区划分自然日，周周期从周一开始。日、周、月收益均使用周期开始前最近一次持久化净值到周期末最后一次净值计算；账户首个周期以初始资金为基准。年化收益为成立以来 CAGR，页面同时显示实际运行天数。运行事件仍通过 `/api/events` 持久化并可查询，但不再占用主导航。

停止服务不会平仓；账户、持仓和策略状态会保存在 SQLite，下一次启动继续运行。暂停操作会取消尚未成交的本地 pending 订单，并继续更新指标状态。
