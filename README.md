# mastermind:tick

Mastermind 旗下短线策略产品。当前版本把“小果量化 ATR Tick Pine v6”并行运行在两个独立模拟账户上，实时记录 Binance SOXLB Spot 与 SOXL TradFi Perpetual 的行情、信号、成交和绩效。

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
ATR 穿越持续确认 = 2 秒
仅做多，100% 账户权益
价格上穿 ATR 线买入，价格下穿 ATR 线平仓
同一根 K 线平仓后禁止再次买入
```

ATR 使用 TradingView `ta.atr` 对应的 Wilder RMA。实时 K 线内，每个 Tick 都从上一根已收盘 K 线的 `source[1]` 和 `tsl_price[1]` 重算止损线，并用 Pine `varip` 语义检测相对 ATR 线的状态变化。启动时会用 Binance REST 当前形成中的 K 线补齐 WebSocket 连接前的 OHLC。

价格首次穿越 ATR 线后进入 2 秒防抖确认；期间回到原侧会取消候选信号，持续位于新侧满 2 秒才产生信号。BUY 和 SELL 都在确认信号的 Tick 立即模拟成交，避免低流动性行情中旧 BUY 订单延迟到下一笔 Tick 后又被立即反向平仓。SOXLB 默认单边手续费 10 bps、滑点 5 bps。SOXL Perpetual 使用 1x 逐仓模型、95% 名义敞口、5 bps taker 手续费和 2 bps 模拟滑点，始终只做多。参数位于 [config/settings.toml](config/settings.toml)。

永续账户按标记价格计算未实现盈亏、初始保证金与可用余额。每 8 小时从 Binance 公共资金费历史同步实际费率，多头资金费以 `-名义价值 × 费率` 计入现金、净值、交易胜率和收益区间。

## 数据

- 实时 SOXLB：`wss://data-stream.binance.vision/ws/soxlbusdt@aggTrade`
- SOXLB 预热：Binance market-data-only REST 的 15 分钟 K 线；
- 实时 SOXL Perpetual：`wss://fstream.binance.com/ws/soxlusdt@trade`；原始成交在 feed 内按 250 ms 聚合，保留数量、名义金额、最高/最低价和底层 Trade ID 范围。
- SOXL Perpetual 预热：`https://fapi.binance.com/fapi/v1/klines` 的 15 分钟 K 线；标记价、指数价和资金费来自 Futures 公共 REST。
- 模拟账本：`data/paper.db`，SQLite WAL 模式。
- `agg_trades`：持久化 Spot aggTrade 或 Futures 250 ms 成交批次、底层交易 ID、事件/成交时间、价格、数量、名义金额和 maker 方向。
- `ohlcv_bars`：保存历史与实时形成中的 15 分钟 OHLCV、底层成交数及开闭状态。
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

- SOXLB 与 SOXL Perpetual 账户切换、实时价格、ATR 移动止损线和 K 线交易锁；
- Futures 标记价、指数价、资金费率、初始保证金、可用余额和累计资金费；
- 实时交易决策状态、信号门控、最近穿越结果及下一触发条件；
- 价格图上的买入/卖出图标、醒目的净交易百分比、区间滚动和缩放；
- 现金、持仓、累计收益、费用和最大回撤；
- 手续费后完整交易胜率和 15 分钟年化夏普率；
- 净值曲线、订单、成交和运行事件；
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

停止服务不会平仓；账户、持仓和策略状态会保存在 SQLite，下一次启动继续运行。暂停操作会取消尚未成交的本地 pending 订单，并继续更新指标状态。
