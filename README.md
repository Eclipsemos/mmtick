# mastermind:tick

Mastermind 旗下短线策略产品。当前版本把“小果量化 ATR Tick V1”运行在独立模拟账本上，实时记录 Binance SOXLB/USDT 的行情、信号、订单、成交、持仓、费用和绩效。

## 产品边界

- `SOXLB/USDT`：Binance 上的 SOXL bStocks 代币化产品，使用 Binance 公共聚合成交实时驱动。
- 单一 SOXLB 模拟账户，初始资金 100,000 USDT。
- 当前是本地 paper broker，不提交真实 Binance 或券商订单。
- Binance Spot Testnet 没有 SOXLB 时，本系统不会用其他测试币伪装 SOXLB。

## 策略与成交

默认配置来自 [strategy_v1.md](strategy_v1.md)：

```text
15 分钟 K 线
ATR Period = 7
ATR Multiplier = 1.0
仅做多，100% 账户权益
价格上穿 ATR 线买入，价格下穿 ATR 线平仓
同一根 K 线平仓后禁止再次买入
```

ATR 使用 TradingView `ta.atr` 对应的 Wilder RMA。信号产生后在下一条行情模拟市价成交，默认单边手续费 10 bps、滑点 5 bps，SOXLB 数量步进为 0.001。参数位于 [config/settings.toml](config/settings.toml)。

## 数据

- 实时 SOXLB：`wss://data-stream.binance.vision/ws/soxlbusdt@aggTrade`
- SOXLB 预热：Binance market-data-only REST 的 15 分钟 K 线；
- 模拟账本：`data/paper.db`，SQLite WAL 模式。
- `agg_trades`：逐条持久化 Binance 聚合成交 ID、底层交易 ID、事件/成交时间、价格、数量、名义金额和 maker 方向。
- `ohlcv_bars`：保存历史与实时形成中的 15 分钟 OHLCV、底层成交数及开闭状态。

行情表采用事件 ID 和 K 线起始时间幂等写入，重连或重启不会重复累计相同成交。仓库页面显示主库、WAL、分表及索引占用；当前未配置自动清理策略，aggTrade 将持续累积。

系统不再读取 SOXL 行情或 `~/mm` 数据。Alpha 的历史研究、真实基金账户与 `mastermind:tick` paper 绩效相互隔离。

## 启动

Python 环境使用 `/home/spaceaic/env/.venv`。前端生产文件已构建到 `frontend/dist`。

```bash
./scripts/run.sh --host 127.0.0.1 --port 8100
```

打开 `http://127.0.0.1:8100`。服务启动后会自动恢复已有账户和策略运行状态，并继续追加净值记录。

页面提供：

- SOXLB 实时价格、ATR 移动止损线和 K 线交易锁；
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
GET  /api/fills.csv
GET  /api/warehouse
GET  /api/market/ohlcv
GET  /api/market/agg-trades
POST /api/control       {"action":"pause" | "resume"}
```

停止服务不会平仓；账户、持仓和策略状态会保存在 SQLite，下一次启动继续运行。暂停操作会取消尚未成交的本地 pending 订单，并继续更新指标状态。
