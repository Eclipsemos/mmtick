# mastermind:tick

Mastermind 旗下短线策略产品。系统使用 Binance 生产环境的公开行情驱动三个相互
隔离的本地模拟账户，并包含一条独立、默认关闭下单的 SOXL USD-M Futures 多空
实盘路径，持续记录行情、策略状态、订单、成交、资金费与账户绩效。

当前策略版本为 `atr_tick_v3_startup_alignment`。从 **2026-08-02 至
2026-08-09** 保持当前策略和仓位参数不变，连续运行一周后再做样本外评估。

## 产品边界

- `SOXLB/USDT`：Binance SOXL bStocks 代币化现货，模拟账户仅做多。
- `SOXL/USDT PERP`：Binance USDⓈ-M `TRADIFI_PERPETUAL`，模拟账户可做多、
  做空。
- `SOXL/USDT PERP LONG ONLY`：同一永续市场的独立仅做多模拟账户，卖出信号
  只平掉已有多头，空仓时不会开空。
- `soxlb`、`soxl_perp` 与 `soxl_perp_long` 各自拥有独立的 100,000 USDT 初始
  资金、订单、持仓、费用和绩效序列。
- 三个绩效账户仍由本地 paper broker 驱动。另有 `soxl_perp_live` 多空实盘账户，
  使用独立的 `data/live_futures.db`，不会把真实余额、订单或成交混入
  `data/paper.db`。
- `soxl_perp_live` 当前以只读观察模式运行；签名账户、持仓、挂单、成交和资金费
  对账已启用，但提交真实订单的配置保持关闭。旧 SOXLB LIVE 运行时已停用，历史
  `data/live.db` 仅保留审计，不再通过 Dashboard 提供。
- Binance Testnet/Demo 暂不提供这两个标的，因此模拟成交使用生产公共行情和本地
  撮合。
- 本项目不读取 `~/mm` 的数据；Mastermind 长线 Alpha 研究和真实基金账户与本项目
  隔离。

## 当前策略

完整规则见 [strategy_v1.md](strategy_v1.md)，运行参数见
[config/settings.toml](config/settings.toml)。当前冻结配置如下：

```text
算法版本                 atr_tick_v3_startup_alignment
K 线周期                 15 分钟
ATR                      Wilder RMA / TradingView ta.atr(21)
ATR 距离                 ATR(21) x 4.0
趋势效率周期             8 根 K 线
最低趋势效率             0.25
反向确认                 0.25 ATR
信号检测                 每个 Binance 成交 Tick
模拟成交                 信号后的下一成交 Tick
频率限制                 每根 15 分钟 K 线最多一个交易动作
```

ATR 移动止损线沿用 Pine 策略的递归规则。启动时使用 Binance 官方 15 分钟 K 线
预热；运行中由成交 Tick 更新当前未收盘 K 线、ATR 和移动止损线。每个 Tick 先用
更新前的价格和止损线判断穿越，再递归更新指标。Binance 官方收盘 K 线用于最终
OHLCV，并在收盘后通过 REST 再校验一次。

价格从 ATR 线下方穿到上方时产生多头方向信号，从上方穿到下方时产生空头方向
信号。新开仓还必须通过趋势效率过滤；该过滤不阻止已有仓位平仓。每根 15 分钟
K 线只有一个全局交易动作名额，以避免同一根 K 线内来回成交。

新账户或新算法首次接收实时 Tick 时会执行一次启动趋势对齐：价格在 ATR 线上方
时尝试建立多头，永续价格在线下时尝试建立空头；SOXLB 现货在线下时保持空仓。
启动对齐状态会持久化，服务重启不会重复开仓。

## 账户与成交

| 账户 | 方向 | 交易所杠杆档位 | 仓位预算 | 目标名义暴露 |
|---|---|---:|---:|---:|
| SOXLB Spot | 仅做多 | 无 | 100% | 1.00x |
| SOXL Perpetual | 多空 | 2x | 62.5% | 1.25x |
| SOXL Perpetual Long Only | 仅做多 | 2x | 62.5% | 1.25x |

永续的 `2x` 是保证金杠杆档位，不代表每次使用 2 倍账户净值。当前仓位预算为
62.5%，因此多头和空头的目标名义暴露均为 **1.25x**。

SOXLB 与 Long Only 永续下穿 ATR 线时只平掉已有多头。多空 SOXL 永续发生反向
穿越时先以 `reduce_only`
平仓，不在同一个信号内立即反手；平仓成交后的下一根 15 分钟 K 线，价格相对
平仓信号锚点继续向目标方向突破 `0.25 ATR`，才建立反向仓位。确认机会只在该根
K 线内有效。

所有模拟成交均按 Taker 计费，并叠加配置的模拟滑点：

- SOXLB Spot：单边手续费 10 bps，滑点 5 bps。
- SOXL Perpetual：单边手续费 5 bps，滑点 2 bps。
- 永续每 8 小时同步 Binance 公共资金费历史；正费率时多头支付、空头收取。

## 一周 Paper 观察

观察窗口为 **2026-08-02 至 2026-08-09**。期间不因短期盈亏调参、不切换算法、
不重建模拟账本，以保证这一周的结果可解释。只有服务故障、行情中断、重复成交或
账本错误等实现问题需要立即修复，并应保留修复记录。

每日检查：

- 服务和 Binance 行情连接状态，是否存在长期未成交的 pending 订单；
- OHLCV、聚合成交、净值快照和资金费是否连续持久化；
- 三账户净收益、最大回撤、完整交易数、胜率和手续费；
- 单 K 线动作锁的拦截是否符合预期；
- 下跌行情中的平仓延迟，以及永续反向确认是否触发或过期。

2026-08-09 复评时，重点比较三个账户的净收益、最大回撤、完整交易数、胜率、
手续费占毛收益比例，并按上涨、下跌和震荡区间拆分结果。SOXLB 的离场速度和
SOXL 永续的反向确认机会成本需要单独审查。

## 数据仓库与持久化

- Spot 实时成交：`wss://data-stream.binance.vision/ws/soxlbusdt@aggTrade`。
- Futures 实时成交：`wss://fstream.binance.com/ws/soxlusdt@trade`，feed 内按
  250 ms 聚合并保留底层 Trade ID 范围。
- 官方 15 分钟 K 线：Spot/Futures `@kline_15m`，每根收盘后分别通过 Binance
  Spot/Futures REST `/klines` 校验。
- 永续标记价、指数价和资金费率来自 Binance Futures 公共接口。
- `soxl_perp` 与 `soxl_perp_long` 复用同一条实时 Futures 行情流以及同一份
  `agg_trades`、`ohlcv_bars`、`funding_rates` 市场仓库；Tick 只存一次，再分别
  驱动两个独立策略状态和账户账本。
- 主数据库：`data/paper.db`，SQLite WAL 模式。
- 实盘数据库：`data/live_futures.db`；保存 USD-M 账户净值、保证金、实际多空持仓、
  订单、成交、手续费、资金费和 ATR 状态。`live_cash_flows` 单独保存经确认的入金和
  出金，策略收益使用资金流调整后的时间加权收益。
- `agg_trades`：保存事件时间、成交时间、价格、数量、名义金额、maker 方向和交易
  ID；Futures 保存 250 ms 聚合批次及底层 ID 范围。
- `ohlcv_bars`：保存历史和实时 15 分钟 OHLCV；官方最终值覆盖当前 K 线临时值。
- `funding_payments`：保存费率、标记价格、持仓名义价值和资金费收付。

成交以事件 ID、K 线以起始时间幂等写入，重连或重启不会重复累计同一条行情。
账户、订单、成交、持仓、策略运行状态和净值快照均持久化。当前没有自动清理策略，
聚合成交数据和 SQLite WAL/索引占用需要持续监控。

## SOXL Futures 实盘接入

`soxl_perp_live` 复用现有 SOXL Futures 公共 Tick、标记价和官方 15 分钟 K 线，
但使用独立 ATR 策略状态和实盘账本。余额、保证金、LONG/SHORT 持仓、挂单、订单、
成交和资金费以 Binance USD-M 签名接口为准；系统不会用下一 Tick 伪造实盘成交。
每个信号生成确定性的 `clientOrderId`，网络结果不明确时按该 ID 对账，不直接重发。

真实订单必须同时满足以下三项配置才可能提交：

```text
live_futures.enabled = true               # 当前已开启只读运行时
live_futures.allow_order_submission = true
MMTICK_LIVE_CONFIRM=SOXLUSDT_PERP_LIVE
```

运行时还要求 Futures test order 已成功并记录在独立实盘库中。测试命令只调用
Binance `/fapi/v1/order/test`，不会创建订单或持仓：

```bash
.venv/bin/mmtick-live-preflight --test-order
```

如果返回 `-4411`，在确认已经阅读并同意 Binance TradFi-Perps 协议后，可通过签名
接口接受协议并紧接着重新执行无成交测试订单：

```bash
.venv/bin/mmtick-live-preflight --sign-tradfi-contract --test-order
```

`--sign-tradfi-contract` 会调用 `/fapi/v1/stock/contract` 并改变账户的协议接受状态，
不得在账户所有者未明确同意协议时使用。

此外还必须有通过签名校验的 Binance Futures `canTrade` 权限、IP 白名单、Hedge
Mode、目标 `2x isolated`、完整对账，且不存在人工挂单、其他合约持仓、未接管的
SOXL 持仓、持久暂停或风控阻断。新实盘账户首次启动只等待新的 ATR 穿越，不会按
启动时所处趋势立即追单。

目标杠杆为 2x、仓位预算为净值的 62.5%，目标名义暴露为 1.25x。实盘测试按操作员
要求关闭单笔名义上限、每日订单数和每日亏损入场限制（对应配置值均为 `0`）；盘口
偏离仍不得超过 30 bps，交易所规则校验、幂等对账和 `reduce_only` 平仓保护仍启用。
ATR 止损由本服务收到实时 Tick 后提交市价平仓，并非预挂在 Binance 的原生止损单；
服务、网络或机器中断期间不会触发该止损。

当前只读凭证从被 Git 忽略的项目根目录 `.env` 读取 `API_KEY` 与 `SECRET_KEY`，文件
权限必须为 `600`；程序不会记录或通过 API 返回密钥。截至 2026-08-04，权限审计结果为
Futures 读取及交易权限开启、提现关闭、IP 白名单开启；账户为 Single-Asset、Hedge
Mode，`SOXLUSDT` 为 2x isolated，TradFi-Perps 协议和 Futures test order 均已通过。
账户已注资并开启订单开关和独立启动确认；全部运行时准入通过时状态为 `ARMED`。
Dashboard 公开接口只包含同步健康和权限状态；实盘余额、仓位、订单、成交和收益必须先建立
独立的操作员会话才能读取。

操作员令牌位于被 Git 忽略的 `data/operator.token`，权限必须为 `600`，不得使用
Binance API Secret 代替。浏览器从本机 `127.0.0.1` 访问时点击 `LIVE` 可自动建立
会话；从其他机器访问时首次点击需要输入操作员令牌。会话使用 HttpOnly、
SameSite=Strict Cookie，8 小时后失效，之后 `PAPER / LIVE` 切换均为一键操作。
远程访问应在 HTTPS 反向代理之后提供，避免在明文 HTTP 链路上传输会话。

不要把密钥写入聊天、仓库、`settings.toml` 或提交记录。部署顺序为：只读签名账户、
持仓、挂单、成交和资金费同步；固定出口 IP；切换为 Single-Asset Margin；将
`SOXLUSDT` 调整为 Hedge Mode 下的 2x isolated；签署 TradFi Perpetuals 协议；
运行 Futures test order；小额观察；
最后才打开配置订单开关和独立启动确认。任何一步失败都不得进入下一步，网页没有
启用真实下单的控制入口。

## 后台运行

Python 环境位于项目根目录 `.venv`，前端生产文件构建到 `frontend/dist`。
本地启动命令：

```bash
./scripts/run.sh --host 127.0.0.1 --port 8100
```

Dashboard“实盘交易”地址为 `http://127.0.0.1:8100`。生产实例由 `mmtick.service` 在后台运行，
网页不需要保持打开；关闭浏览器不会停止行情处理、策略执行或持久化。检查服务：

```bash
systemctl --user status mmtick.service
journalctl --user -u mmtick.service -f
```

停止服务不会自动平仓。重启后会恢复已有账户、持仓和兼容的策略状态。Dashboard
暂停操作会取消尚未成交的本地 pending 订单，但仍继续接收行情和更新指标。

## Dashboard

页面提供：

- 顶部 `PAPER / LIVE` 一键模式切换；PAPER 保留三个模拟账户，LIVE 只显示独立的
  `SOXL/USDT PERP LIVE` Binance USD-M Futures 多空账户；
- LIVE 模式复用监控、订单、收益和仓库组件，真实账户余额与成交使用受保护接口，
  OHLCV 和 aggTrade 继续使用共享的 Binance 公共行情仓库；
- `SOXL/USDT PERP LIVE` 提供持久停止策略按钮，以及带二次确认的人工平仓按钮；
  人工平仓会重新读取 Binance 实际仓位，只减仓且在存在挂单或未决订单时拒绝执行；
- SOXLB、SOXL Perpetual 多空及 SOXL Perpetual Long Only 账户切换、行情连接
  和策略运行状态；
- 可滚动、缩放的价格与交易信号图，以及独立的官方 15 分钟 K 线图；
- 价格快照和 OHLCV 按时间游标加载历史页，价格线按图表像素密度降采样；
- 黄色 ATR 移动止损线，以及做多、做空和平仓成交标记；
- 持仓期间显示当前成本价、当前价平仓收益、ATR 平仓价及按当前止损线估算的毛收益；
- 当前价格、ATR、趋势效率、K 线动作锁、反向确认和下一触发条件；
- Futures 标记价、指数价、资金费率、保证金、可用余额和累计资金费；
- 现金、持仓、净值、累计收益、最大回撤、胜率和年化夏普率；
- LIVE 账户净值同时标明外部净入金；入金和出金不计入累计收益、周期收益、最大回撤
  或夏普率；
- 每笔完整交易的方向、进出场时间、手续费后盈亏；
- 最近 30 个自然日收益日历、最近 12 周、最近 12 月和成立以来 CAGR；
- OHLCV/aggTrade 的覆盖时间、记录数和磁盘占用；
- 信号暂停/恢复和成交 CSV 导出。

策略参数只在 LIVE 的 `STRATEGY STATE` 中默认隐藏，用户点击后才显示；PAPER 不展示
这组隐藏参数。

## 开发与验证

```bash
export PYTHONPATH="$PWD/src"
.venv/bin/pytest
.venv/bin/ruff check src tests

cd frontend
npm run build
```

前端开发服务器：

```bash
cd frontend
npm run dev
```

Vite 会把 `/api` 转发到 `127.0.0.1:8100`。

## API

```text
GET  /api/health
GET  /api/overview
GET  /api/live/readiness
GET  /api/live/session
POST /api/live/unlock
POST /api/live/unlock-local
POST /api/live/logout
POST /api/live/control       {"action":"stop"}
POST /api/live/flatten       {"confirm":"FLATTEN_SOXLUSDT"}
GET  /api/live/overview
GET  /api/live/equity?limit=&before_ms=
GET  /api/live/returns
GET  /api/live/orders
GET  /api/live/fills
GET  /api/live/funding
GET  /api/live/events
GET  /api/live/fills.csv
GET  /api/accounts/{id}/equity?limit=&before_ms=
GET  /api/accounts/{id}/returns
GET  /api/orders
GET  /api/fills
GET  /api/events
GET  /api/funding
GET  /api/fills.csv
GET  /api/warehouse
GET  /api/market/ohlcv?instrument_id=&limit=&before_ms=
GET  /api/market/agg-trades
POST /api/control       {"action":"pause" | "resume"}
```

收益明细按浏览器本地时区划分自然日，周周期从周一开始。日、周、月收益使用周期
开始前最近一次净值到周期末最后一次净值计算；账户首个周期以初始资金为基准。
年化收益是成立以来 CAGR，并同时显示实际运行天数。运行仅一两天时，CAGR 会被
极度放大，不适合作为策略质量判断依据。

## Tick 回放

可使用仓库中已持久化的 `agg_trades` 做无未来数据的 Tick 级参数回放：

```bash
PYTHONPATH=src .venv/bin/python -m mastermind_tick.backtest
```

回放沿用 Tick 穿越、启动对齐、趋势效率过滤、分阶段反向、单 K 线动作锁和下一
Tick 成交语义，并计入各账户的 Taker 手续费、滑点、目标暴露和永续历史资金费。
报告写入 `reports/`，不会修改模拟盘数据库。

三个利润退出版本（当前基准、固定 6 ATR 止盈、2 ATR 激活后使用 2.5 ATR
利润保护）的复现命令：

```bash
PYTHONPATH=src .venv/bin/python -m mastermind_tick.profit_backtest \
  --cutoff-ms 1785739041994
```

冻结至 `2026-08-03 14:37:21.994 UTC+8` 的报告见
[reports/profit_exits/atr_profit_exit_comparison_20260803T063734Z.md](reports/profit_exits/atr_profit_exit_comparison_20260803T063734Z.md)。

当前冻结参数的复现命令：

```bash
PYTHONPATH=src .venv/bin/python -m mastermind_tick.backtest \
  --periods 21 \
  --multipliers 4.0 \
  --end-ms 1785652557073 \
  --minimum-return 0.20 \
  --output-dir reports/validation
```

冻结样本报告见
[reports/sample_target_validation_20260802.md](reports/sample_target_validation_20260802.md)：
SOXLB 为 `+29.83%`，SOXL Perpetual 为 `+23.00%`，双倍手续费和滑点压力测试仍
超过 20%。但该数据不足三天，而且参数是在同一段数据上优化和验证的，只能证明
回放实现与样本内结果，不能作为未来收益预期。这也是当前需要冻结参数运行一周、
积累样本外数据的原因。

## 风险与限制

- Paper 成交使用下一笔公开成交和固定滑点模型，未完整模拟盘口深度、排队、拒单、
  限价成交、强平和真实 API 延迟。
- SOXLB 和 SOXL Perpetual 的成交量、交易时段、跟踪误差及产品规则可能变化。
- ATR 趋势策略在窄幅震荡中可能频繁止损；趋势效率和反向确认只能降低噪声，不能
  消除亏损。
- 杠杆和做空会放大收益、亏损与资金费影响。上线真实资金前必须重新验证交易规则、
  精度、保证金、风控和故障恢复。
- 当前回放样本过短。一周 paper 观察仍只是初步验证，不足以支持真实资金决策。
