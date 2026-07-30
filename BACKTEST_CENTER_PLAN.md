# Binance 股票类回测中心建设计划

> 版本：v0.1  
> 日期：2026-07-30  
> 目标：先建设一个可复现、可审计的 Binance 股票相关标的回测中心，再把同一套策略与风控逻辑接入 Binance Testnet / Demo Trading 做仿真验证。

## 1. 结论与实施原则

### 1.1 推荐路线

不把“Binance 原生股票”当作已经存在且长期可用的固定产品。系统启动时应根据账户地区、产品权限和 Binance `exchangeInfo` 的实时结果生成可交易标的库，并按以下优先级选择：

1. **Binance 可交易的股票代币化现货**：如果生产环境实际存在、状态为 `TRADING`、账户所在地区允许交易，则优先使用。
2. **Binance 可交易的股票/指数衍生品**：如果存在对应 USD-M 合约，则回测必须加入资金费、标记价格、杠杆和强平模型。
3. **Binance 上架的第三方代币化股票现货**：必须同时取得基础股票参考价、交易时段和公司行为数据，衡量代币与基础资产的偏离。
4. **对应的 Binance 现货代理标的**：若以上均不可用，再使用与目标公司或行业相关的加密现货/合约；必须标记为 `proxy`，不能把结果解释为股票收益。

第一版建议以 **Binance Spot + USD-M Futures 的统一回测能力** 为核心。标的注册表允许以后接入真实股票、代币化股票和其他交易所，而无需重写策略。

### 1.2 Testnet 的角色

- **历史回测**：使用 Binance 生产环境公开历史数据和 Binance Data Collection，不使用 Testnet 生成研究样本。
- **仿真交易**：使用 Spot Testnet 或 Futures Demo Trading 验证下单、撤单、成交回报、仓位同步、断线恢复和风控。
- **上线前验证**：同一策略代码依次运行在 `backtest -> paper -> testnet/demo -> live-disabled` 四种模式，默认禁止真实交易。
- Testnet 标的、流动性和成交行为可能与生产环境不同，也可能被重置，因此 Testnet 结果只用于接口和运行稳定性验证，不用于证明策略盈利能力。

### 1.3 MVP 边界

MVP 支持：

- Spot 和 USD-M Futures；
- K 线级回测，第二阶段扩展到 `aggTrades` 级回放；
- 市价单、限价单、止损/止盈单的统一订单模型；
- 单标的及多标的组合；
- 手续费、滑点、资金费、价格/数量精度、最小名义金额；
- 股票类代币的交易时段、参考价偏离和公司行为扩展字段；
- 参数实验、结果对比、报告导出和 Testnet 仿真。

MVP 不做：高频逐档撮合、期权、跨交易所实盘、自动寻找“最优策略”、真实资金下单。

## 2. 标的范围与准入机制

### 2.1 统一标的分类

| 类型 | `instrument_type` | 价格含义 | 回测必需数据 |
|---|---|---|---|
| Binance 普通现货 | `spot` | 24/7 加密资产成交价 | K 线/成交、手续费、交易规则 |
| 代币化股票现货 | `tokenized_equity` | 代币成交价，不保证等于股票现价 | 上述数据 + 股票参考价、公司行为、交易时段 |
| USD-M 永续合约 | `perpetual` | 合约价格 | 成交价、标记价、指数价、资金费、杠杆档位 |
| 交割合约 | `delivery_future` | 到期合约价格 | 上述数据 + 到期日、交割规则 |
| 股票/行业代理标的 | `proxy` | 相关加密资产价格 | 映射依据、滚动相关性、跟踪误差 |

### 2.2 标的注册表

每个标的至少保存以下带生效时间的字段，禁止只保留“当前状态”：

```text
instrument_id, venue, market, symbol, instrument_type
underlying_id, quote_asset, contract_size, expiry
tick_size, step_size, min_notional, price_precision, qty_precision
trading_status, onboard_date, delist_date, valid_from, valid_to
session_calendar, timezone, corporate_action_source
reference_symbol, reference_venue, proxy_reason, region_eligibility
```

每日保存一次生产和测试环境的 `exchangeInfo` 快照。回测按当日有效规则执行，避免用今天的标的列表和精度规则回测过去，从而引入幸存者偏差。

### 2.3 启动阶段的标的可行性 Gate

项目第 1 周先输出 `instrument_feasibility.csv`，每个候选标的必须通过：

1. 生产环境 `exchangeInfo` 中存在，且状态可交易；
2. 当前账户地区和账户类型拥有权限；
3. 至少有 6～12 个月可获得的历史数据；
4. 日均成交额、价差和缺失率达到预设阈值；
5. 若为合约，资金费、标记价和杠杆规则完整；
6. 若为代币化股票，能取得可靠的基础资产参考价和公司行为数据；
7. 法务/合规确认其持有人权利、地区限制和数据使用条款。

不满足条件的标的只进入观察列表，不进入正式回测排名。

### 2.4 代理标的规则

只有在无法获得直接股票类产品时才启用代理映射。映射必须记录版本、理由和有效期，并至少报告：

- 与目标股票日收益的 60/120 日滚动相关性；
- beta、跟踪误差和最大偏离；
- 美股交易时段内与时段外的分别统计；
- 映射失效阈值，例如 60 日相关性低于 0.5 时停止使用。

代理标的回测报告标题必须包含“代理”，不得与股票或股票代币的报告混排。

## 3. 系统架构

```text
Binance REST/WebSocket/Data Archive       外部参考数据（按需）
                 |                         股票日历/公司行为/参考价
                 +--------------+------------------+
                                v
                    数据采集与完整性校验
                                |
                     Raw -> Normalized -> Curated
                                |
                Parquet/Object Storage + DuckDB
                                |
       +------------------------+------------------------+
       |                        |                        |
   事件回放引擎             策略与组合引擎            实验管理
       |                        |                        |
       +------------- 执行/费用/风控模型 ---------------+
                                |
                     结果库 + FastAPI 服务
                                |
                  Web 控制台 / 报告 / Testnet
```

核心约束：

- **同一份策略逻辑**用于回测和仿真，市场数据源与订单执行器通过接口切换。
- 原始数据只追加，不原地修改；清洗后的数据携带来源、校验和与版本。
- 所有实验都绑定代码版本、配置、标的快照和数据集快照。
- 金额与数量使用定点小数，不使用二进制浮点数处理订单精度。

## 4. 建议技术栈

| 层 | MVP 选型 | 原因 |
|---|---|---|
| 语言 | Python 3.12 | 研究生态和服务开发可共用 |
| 数据处理 | Polars + PyArrow | 大规模列式处理、Parquet 兼容 |
| 本地分析 | DuckDB | 直接查询 Parquet，部署简单 |
| 元数据/结果 | PostgreSQL | 实验、任务、权限和查询状态 |
| API | FastAPI + Pydantic | 类型明确，便于生成 OpenAPI |
| 异步任务 | Celery/RQ + Redis（二选一） | 长时间回测、下载和重试 |
| 回测内核 | 自建小型事件驱动内核 | 准确表达 Binance 过滤器、资金费和 Testnet 订单语义 |
| 前端 | React + TypeScript | 构建实验、图表和对比界面 |
| 图表 | Lightweight Charts + ECharts | K 线、成交点及组合分析 |
| 部署 | Docker Compose 起步 | 本地和单机服务器可重复部署 |

是否采用现成回测库，应在第 1 周做一个小型验证。可以复用其指标和数据结构，但 Binance 规则、代币化股票日历、公司行为和成交模型必须由本项目的适配层控制，避免被库的默认假设隐藏。

## 5. 数据方案

### 5.1 数据层级

- `raw`：下载原文件或 API 响应，保留 URL、获取时间、HTTP 元数据和 SHA-256。
- `normalized`：统一 UTC 时间、字段名、精度和 schema；保留来源行定位信息。
- `curated`：按策略频率生成无缺口 K 线、成交、标记价、资金费和参考价视图。
- `features`：按数据集快照生成特征；特征必须记录 `available_at`，防止未来数据泄漏。

### 5.2 Binance 数据清单

Spot：

- `exchangeInfo`；
- `klines`，MVP 使用 `1m` 作为最低粒度并向上聚合；
- `aggTrades`，用于成交量参与率和滑点校准；
- 费率、账户等级和交易规则快照。

USD-M Futures：

- 合约 `exchangeInfo` 和杠杆/名义金额档位；
- 普通 K 线、标记价格 K 线、指数价格 K 线；
- 资金费率和资金费结算时间；
- `aggTrades`、持仓量（可获得时）；
- 合约上线、下线和交割信息。

代币化股票额外数据：

- 基础股票的复权与未复权价格；
- 交易所日历、时区和盘前/盘后规则；
- 拆股、合股、分红、并购、停牌和退市；
- 代币赎回/铸造规则、储备或发行人事件（若产品适用）；
- 代币价格相对基础股票净值的溢价/折价。

### 5.3 时间与质量规则

- 内部时间统一为 UTC；UI 可切换 Asia/Tokyo、America/New_York。
- Binance Spot 归档从 2025-01-01 起时间戳可能为微秒，入库时必须自动识别并标准化，不能固定按毫秒解析。
- 禁止静默前向填充成交价或资金费；缺口必须形成质量事件。
- 每个分区检查单调时间、重复键、OHLC 合法性、负值、成交量异常和预期覆盖率。
- 数据修订后生成新快照，不覆盖旧实验依赖的数据。

## 6. 回测引擎设计

### 6.1 事件顺序

每个时间点固定使用以下顺序，并写成测试用例：

1. 发布本时点可见的市场数据；
2. 处理公司行为、资金费、交割或交易状态变更；
3. 策略读取截至当前可用的数据并产生目标仓位/订单；
4. 风控检查余额、名义金额、杠杆、限额和 Binance filters；
5. 执行模型依据下一可成交事件决定成交、部分成交或拒单；
6. 更新现金、仓位、保证金、未实现盈亏和订单状态；
7. 记录审计事件和净值。

信号在 K 线收盘生成时，默认最早在下一根 K 线成交，除非策略明确使用盘中数据。这一规则用于阻止收盘价偷看。

### 6.2 成交与成本模型

MVP 提供三个等级：

- `close_plus_bps`：下一根 K 线开盘/收盘价加固定滑点，只用于策略初筛；
- `bar_volume`：根据点差估计、波动率和成交量参与率计算冲击；
- `trade_replay`：使用 `aggTrades` 回放，模拟限价订单排队的保守上界和下界。

成本至少包括：maker/taker 手续费、滑点、资金费、借贷成本（若适用）、换汇成本和强平费用。不得用零成本结果作为正式排名。

### 6.3 组合与风险

- 现货现金账户与合约保证金账户分账；
- 支持逐仓/全仓配置，但 MVP 正式结果先限定逐仓；
- 杠杆、单标的权重、行业/主题敞口、日换手和成交量参与率上限；
- 维护保证金、标记价格触发、强平及负余额保护；
- 数据中断、标的停牌、状态变为非交易时禁止开仓并执行预设退出政策；
- 策略级、组合级和账户级熔断器。

### 6.4 评估方法

必须输出：年化收益、年化波动、Sharpe、Sortino、最大回撤、Calmar、胜率、盈亏比、换手、成本占毛收益、资金费贡献、容量估计、敞口和回撤持续时间。

研究流程固定为：

```text
训练区间 -> 验证区间 -> 完全隔离的测试区间 -> Walk-forward -> Testnet 仿真
```

参数搜索需报告全部实验而非只保留最佳值，并加入多重比较/过拟合提示。股票代理策略还要单独报告跟踪误差和映射稳定性。

## 7. 策略接口与首批基线

策略只返回目标仓位或标准订单，不直接调用 Binance SDK：

```python
class Strategy:
    def on_start(self, context): ...
    def on_bar(self, context, bars): ...
    def on_trade(self, context, trade): ...
    def on_order_update(self, context, event): ...
    def on_stop(self, context): ...
```

首批基线用于验证系统，不代表投资建议：

1. 单标的移动平均趋势；
2. 多标的横截面动量；
3. 代币化股票相对基础股票参考价的溢价/折价监控；
4. 合约与对应现货的基差/资金费策略；
5. 股票代理标的的 beta 对冲实验。

每个策略必须附带参数 schema、所需数据、最大杠杆、预热期和允许标的类型。

## 8. Testnet / Demo Trading 接入

### 8.1 适配器

定义统一接口：

```text
MarketDataAdapter: subscribe, snapshot, history
BrokerAdapter: place, cancel, query_order, positions, balances
Clock: historical, realtime
RiskGateway: validate_order, reconcile, kill_switch
```

实现 `BacktestBroker`、`SpotTestnetBroker` 和 `FuturesDemoBroker`。生产 Broker 即使后续实现，也默认编译/配置为禁用，必须经过单独审批才能启用。

### 8.2 仿真验收场景

- 市价、限价、部分成交、撤单和订单拒绝；
- 超过价格/数量精度、低于最小名义金额；
- REST 超时后通过用户数据流和订单查询确认最终状态；
- WebSocket 断线、listen key 续期、重连和事件去重；
- 服务重启后从交易所账户恢复订单与仓位；
- 本地仓位与交易所仓位不一致时停止开仓并告警；
- API 限频、`429/418` 退避和熔断；
- Testnet 重置后的账户状态识别。

API Key 只通过 secret manager 或本地未提交的环境文件注入；不同环境使用独立 key，禁止开启提现权限，日志禁止输出签名和凭据。

## 9. 后端 API 与前端页面

### 9.1 后端 API

```text
GET  /instruments                 标的与当前可用性
GET  /datasets                    数据覆盖和质量
POST /backtests                   创建实验
GET  /backtests/{id}              状态与摘要
GET  /backtests/{id}/events       订单/成交/风控事件
GET  /backtests/{id}/artifacts    净值、持仓和报告
POST /paper-sessions              本地实时仿真
POST /testnet-sessions            Binance 测试环境会话
POST /sessions/{id}/stop          停止策略并按政策处理挂单
```

所有创建类请求支持幂等键。回测配置经过 schema 校验后冻结，结果可通过 `run_id` 完整追溯。

### 9.2 MVP 页面

- **标的库**：类型、市场、交易状态、数据范围、流动性、准入失败原因；
- **策略实验**：选择策略、数据快照、区间、成本模型、参数和风险限额；
- **运行中心**：排队/运行/失败状态、日志和资源消耗；
- **结果分析**：净值、回撤、持仓、成交、费用分解、月度收益和基准对比；
- **实验对比**：同策略不同参数或不同数据区间并排比较；
- **Testnet 监控**：连接状态、余额、订单、仓位、风控和一键停止。

## 10. 建议目录结构

```text
mmtick/
  apps/
    api/
    web/
    worker/
  src/mmtick/
    domain/            # instrument/order/fill/portfolio 等纯领域模型
    data/              # 下载、校验、schema、数据集快照
    exchanges/binance/ # Spot/Futures/Testnet 适配器
    backtest/          # 时钟、事件循环、撮合、费用
    strategies/        # 策略接口和基线策略
    risk/              # 下单前和运行时风控
    reporting/         # 指标和报告
  migrations/
  tests/
    unit/
    integration/
    replay/
  configs/
  docs/
  docker-compose.yml
```

领域模型不得依赖 Binance SDK 的响应对象；SDK/HTTP JSON 只能在 `exchanges/binance` 内转换，以便测试和未来更换数据源。

## 11. 分阶段交付计划

以下估算按 2 名工程师计算；若仅 1 人实施，建议按 1.5～2 倍日历时间安排。

### Phase 0：产品与标的可行性（2～3 天）

- 获取目标账户地区、Spot/Futures 权限和候选股票清单；
- 生产/Testnet/Demo `exchangeInfo` 探测；
- 产出标的分类、历史覆盖、流动性和外部数据缺口表；
- 确定第一批 5～20 个标的，以及是否启用代理路线。

**验收**：每个标的都有 `direct / tokenized / derivative / proxy / rejected` 结论和证据；没有未验证的 symbol 进入开发配置。

### Phase 1：数据底座（第 1～2 周）

- 建立项目骨架、配置、CI 和容器环境；
- 实现 Spot/期货元数据快照和历史文件下载；
- 完成 raw/normalized/curated 分层、校验和数据目录；
- 建立 1m K 线、资金费、标记价和指数价 schema；
- 建立可复现的 `dataset_snapshot_id`。

**验收**：指定标的 1 年数据可一条命令构建；重复运行幂等；缺口、重复和时间戳单位测试通过；任一记录可追溯至原文件。

### Phase 2：回测内核（第 2～4 周）

- 实现事件时钟、订单状态机、组合账本和 Binance filters；
- 实现 Spot 手续费/滑点以及 Futures 标记价/资金费/强平；
- 实现策略 API 和两项基线策略；
- 实现指标、结果 artifact 和确定性回放。

**验收**：相同代码、配置和数据快照运行两次得到相同结果；手工小样本账本逐笔对平；无未来函数测试通过。

### Phase 3：实验服务与 Web MVP（第 4～5 周）

- FastAPI、任务队列、实验数据库和进度事件；
- 标的、数据集、创建实验、结果与对比页面；
- 权益、回撤、持仓、成交和成本分析；
- 失败重试、取消任务和资源配额。

**验收**：用户可在页面完成“选标的 -> 配策略 -> 运行 -> 分析 -> 导出”闭环；并发任务不会污染数据或结果。

### Phase 4：Testnet / Demo 仿真（第 5～6 周）

- 接入 Spot Testnet 与 Futures Demo 中实际可用的一项或两项；
- WebSocket 用户事件、订单幂等、重连和定时对账；
- 实现环境隔离、密钥管理、熔断和一键停止；
- 基线策略连续仿真运行。

**验收**：连续运行 7 天无未解释仓位差异；重启/断网恢复测试通过；任何异常都不会自动切换到生产交易。

### Phase 5：股票类增强与上线评审（第 7～8 周，可选）

- 接入基础股票参考价、交易日历和公司行为；
- 代币溢价/折价与代理跟踪误差报告；
- `aggTrades` 回放和更真实的容量/冲击模型；
- 性能、权限、数据许可、地区合规和灾难恢复评审。

**验收**：拆股/分红/停牌/退市测试样例账本正确；股票类报告明确区分直接资产、代币、衍生品和代理资产。

## 12. 测试策略

- **单元测试**：金额精度、过滤器、订单状态机、手续费、资金费、公司行为和指标；
- **性质测试**：无交易时现金守恒、成交后资产负债平衡、未来数据不可访问；
- **Golden replay**：保存小型行情和预期订单/成交/净值，防止引擎升级改变历史结果；
- **集成测试**：Binance 公共 API contract test；私有接口仅在显式 Testnet CI 环境运行；
- **故障测试**：超时、重复/乱序事件、断线、限频、数据缺口和 Testnet 重置；
- **性能测试**：目标为单机在可接受时间内完成 20 标的、1 年、1m K 线回测；Phase 1 后根据样本确定具体 SLA。

## 13. 主要风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| Binance 不提供目标股票类产品 | 无法按原标的交易 | Phase 0 动态准入，转代币化/衍生品/代理路线 |
| 地区或账户限制 | API 可见但账户不可交易 | 同时验证公开元数据和账户权限，不绕过地域限制 |
| Testnet 与生产不一致 | 仿真结果误导 | Testnet 只验接口；研究使用生产公开行情和保守成交模型 |
| 代币不等同于股票 | 权利和价格表现不同 | 单独资产类型、参考价偏离和发行人风险披露 |
| 公司行为处理错误 | 收益和持仓严重失真 | 双价格序列、事件账本和拆股/分红 golden tests |
| 24/7 代币对非 24/7 股票 | 闭市时段价格脱锚 | 分时段统计，闭市时限制策略并提高滑点 |
| 下线和历史幸存者偏差 | 回测虚高 | 保存历史 `exchangeInfo`、上线/下线事件和失败标的 |
| 过拟合 | 实盘失效 | 隔离测试集、walk-forward、全量实验记录和成本压力测试 |
| API/归档格式变化 | 数据解析错误 | schema 版本、contract tests 和异常隔离区 |

## 14. 项目决策点

开工前只需要确定以下业务输入，其余技术细节可按本计划默认值推进：

1. 第一批希望覆盖的股票名单，例如 10～20 个美股代码；
2. 可使用 Binance 的账户注册地区，以及 Spot、Futures 权限；
3. 是否允许接入一个外部股票行情/公司行为数据源；
4. 策略时间尺度：日线/小时级，还是必须支持分钟级；
5. 预计并发用户数、回测并发数和部署位置；
6. MVP 是否只做研究，还是必须在第一个版本连接 Testnet。

如果这些信息暂时未定，默认采用：10 个高流动性候选标的、允许外部参考数据、最低 1m 数据、单用户/4 个并发任务、MVP 包含 Spot Testnet。

## 15. 完成定义

回测中心 MVP 在同时满足下列条件时视为完成：

- 标的可用性来自运行时探测且有历史快照；
- 数据、代码、配置和结果均可版本化与复现；
- Spot 和至少一种 Futures/股票相关路线可统一回测；
- 正式结果包含真实费用、滑点和适用的资金费/公司行为；
- 通过确定性、无未来函数、账本守恒和故障恢复测试；
- Web 端可完成完整实验闭环；
- Testnet 连续运行 7 天且本地与交易所账本一致；
- 报告不会把代币、衍生品或代理标的描述为基础股票本身。

## 16. 官方参考

- Binance Spot API 文档：<https://github.com/binance/binance-spot-api-docs>
- Binance Spot Testnet 文档：<https://github.com/binance/binance-spot-api-docs/blob/master/testnet/rest-api.md>
- Binance 公共历史数据：<https://data.binance.vision/>
- Binance 公共数据格式与校验说明：<https://github.com/binance/binance-public-data>
- Binance USD-M Futures API：<https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info>

外部股票数据源和 Testnet/Futures Demo 的最终端点，应在 Phase 0 根据账户地区及当时的 Binance 官方文档确认，并将确认结果固化为带日期的 ADR（Architecture Decision Record）。
