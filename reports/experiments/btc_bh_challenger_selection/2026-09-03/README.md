# BTC B&H Challenger Selection

本报告统一整理当前 BTCUSDT 候选。回放使用已完成 UTC 日线信号、下一根 15m 开盘执行、
50% 现货与 50% 隔离 USD-M 抵押、历史 Funding、每边 10 bps 手续费与 5 bps 滑点；有效
杠杆硬上限为 3X。数据最新完整 15m K 线截至 `2026-09-03T01:14:59.999Z`。

## Strict 15m Candidates

| 候选 | Research 超额 | Validation 超额 | OOS 超额 | Full CAGR | 最大 DD | 峰值杠杆 | 90d Bootstrap P05 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SMA10/40 enter3-exit1 active1.5X | +109.19pp | +122.00pp | +23.26pp | 61.73% | -75.72% | 2.996X | -8.96% |
| SMA11/40 enter2-exit1 active1.6X | +254.67pp | +197.99pp | +44.91pp | 78.21% | -75.37% | 2.783X | -0.13% |
| SMA10/40 enter2-exit1 active1.25X | +233.99pp | -64.03pp | +35.54pp | 64.35% | -63.89% | 1.804X | -7.08% |

`SMA10/40 enter3 active1.5X` 是基于预注册 Research/Validation 成本网格的主候选；
`SMA11/40 active1.6X` 是收益优先的 challenger。两者都在主要分段超过 B&H，且没有历史
强平，但 Bootstrap 下界仍不能证明未来必有正超额。

## Stitched Strict-15m Lead

最强的跨现货/永续版本为 `SMA11/40-enter2-exit1-active1.5X`：2017–2019 使用现货日线，
2020 至今使用 15m 永续执行并逐笔计入 Funding。默认成本下，Research/Validation/OOS 超额
为 `+247.13/+121.44/+43.52pp`，Full CAGR `74.59%`，峰值有效杠杆 `2.602X`。在每边
`20+10 bps` 中度成本下上述三段仍为正；每边 `50+25 bps` 时 Validation 为 `-10.20pp`。
30/90/180/365/730 日 Bootstrap 年化超额 P05 为 `+0.27/+2.81/+7.74/+10.55/+12.07%`，
但 7 日 P05 为 `-2.47%`，Full 历史最大回撤为 `-75.62%`。

## Annual Walk-Forward

一个独立的年度 walk-forward 仅用前序数据从预设 `SMA9–12/40`、1–3 日入熊网格选择下一年
规则，并连续执行至今。2020–2026 共 5/7 年超过 B&H，策略累计 `+3029.13%`、B&H
`+976.30%`、峰值有效杠杆 `2.162X`；2022 和 2024 分别落后 `3.72pp` 与 `43.90pp`，最大回撤
`-76.52%`。这降低了固定参数的选择偏差担忧，但年度数太少，且不能解决尾部回撤问题。

## Cross-Provider Signal Check

冻结的 SMA11/40 参数在独立 Coinbase BTC-USD 完成日线中，以次日开盘和每次变更名义金额
15bps 的简化 1.5X 现货保证金代理复核，Research/Validation/OOS 超额为
`+491.73/+279.00/+51.97pp`。这说明信号不是明显由 Binance 单一收盘价造成；但该审计没有
Funding、隔离抵押、逐 15m 执行或强平，不能替代严格主回放，也不能提高策略状态。

## Tail Concentration

冻结候选严格路径中，移除最强 5/10 个策略相对 B&H 日后仍保持正超额，移除 20 个后则落后
`19894.72pp`。剔除任意一个完整年度仍
超过 B&H。这排除“单日或单一年份制造全部结果”的最简单解释，却表明 Edge 仍依赖少量危机防守
和趋势爆发日，不能据此降低其 `-75.62%` 历史回撤或前向观察门槛。

## Strict-3X Exposure Boundary

在独立的 `2.5X` 合约开仓控制下，以 `0.01X` 步长固定扫描同一 SMA11/40 规则，`1.53X` 是
本历史样本中最后一个盘中峰值不超过 `3X` 的主动暴露（峰值 `2.969X`）；`1.54X` 已达到
`3.008X`。该审计只定义风险可行边界，不能依据同一历史样本把已冻结的 `1.5X` 候选调至
`1.53X`。此外，该边界审计使用 `2.5X` 开仓控制，不能与冻结候选的 `2X` 控制直接比较收益。

## Fixed-Ensemble 3X Exposure Audit

对固定 SMA7/35、SMA8/40、SMA12/40 等权组合预先扫描主动暴露 `1.5/1.6/1.7/1.75/1.8X`，并将
期货开仓与盘中有效杠杆都限制为 `3X`。所有配置历史上均超过 1X B&H；但只有 `1.5X` 在
Research、Validation、OOS 和 Full 分段均未超出硬上限，峰值有效杠杆 `2.996X`。`1.6X` 及以上
在历史盘中达到 `3.132–3.486X`，应视为不可行。`1.5X` 的 Full CAGR `66.10%`、最大回撤
`-75.30%`，90 日 bootstrap 年化超额 P05 `-7.31%`，所以 3X 约束只筛掉超限配置，不能证明
策略具有稳定未来优势。详见 [ensemble exposure audit](../../btc_sma_ensemble_exposure/2026-09-03/README.md)。

## Cost Break-Even

冻结候选的单变量成本审计保留历史 Funding 和全部执行逻辑，只提高每边手续费加滑点。每边总成本
`60 bps` 时 Research/Validation/OOS 超额仍为 `+143.96/+20.13/+22.01pp`；Validation 在
`75 bps` 首次转为 `-10.20pp`。这表明历史超额并非只存在于默认 `15 bps` 成本假设，但仍需
以前向实际费率、滑点和资金费核验，不得把测试点解释为实盘成本保证。

## Levered Benchmark

将冻结策略与持续 `1.5X` BTC 的风险匹配基准置于相同抵押、Funding、成本和盘中保护下，策略在
spot、Research、OOS 与全样本领先，但在 2023–2024 Validation 落后 `39.56pp`。因此它并未在
每个分段持续超过等目标暴露 BTC；原始 1X B&H 超额同时包含动态降低熊市风险和更高主动暴露的
贡献。该反证进一步支持维持 `RESEARCH_ONLY`，而不是把全样本收益解释为稳定的纯择时 Alpha。

进一步对预先定义的 15 个 `active 1.5X` SMA 成员（fast `8–12`、入熊 `1–3` 日）仅按
Research/Validation 与持续 `1.5X` BTC 比较，开发期合格数为 `0/15`。每个成员在 Research
领先，但 Validation 全部落后，最小失败为 `-39.56pp`。OOS 没有被读取，因为没有开发期合格成员。
这拒绝了该 SMA 空仓家族已具备跨阶段、等风险纯 Alpha 的说法。

一个独立的日线 Donchian 突破家族（`20/10`、`55/20`、`100/50`，仅多头、突破离场空仓）在同一
持续 `1.5X` BTC 基准下也未通过：Research 虽为正，但 Validation 分别为
`-437.81/-535.60/-524.63pp`，开发期合格 `0/3`，OOS 未读取。这排除了“只是 SMA 选错”的解释，
但不能证明不存在其他独立 BTC Alpha。

日线均值回归（Bollinger `20/50 × 1.5/2.0σ`、RSI `14/21 × 30/35`，超卖买入、回到中心空仓）
也全部失败：开发期合格 `0/8`，Validation 相对持续 `1.5X` BTC 为 `-459%` 至 `-624%`。因此目前
尚无证据支持用简单低频反转规则取代持续 BTC 暴露。

独立的 Binance 期货市场指标筛选也未通过。使用 4h 已完成的 OI 变化、主动买卖比、账户及仓位
拥挤度（3 个归一化窗口、3 个 z 阈值和 follow/fade，共 `144` 个预先定义的 long-only `0X/1.5X`
配置）与持续 `1.5X` BTC 比较，开发期合格数为 `0/144`。最佳全局拥挤度反转在 Research 领先
`243.08pp`，但 Validation 落后 `261.02pp`；OOS 未读取。结果拒绝“单一 Binance 市场指标可稳定
提供 BTC 等风险超额”的假设，详见 [market-metric screen](../../btc_metric_matched_benchmark/2026-09-03/README.md)。

极端 Funding 事件也未提供独立等风险超额。以此前 `30/90/180` 个结算事件归一化，测试
`1/1.5/2/2.5/3σ`、`1–12` 个 4h bar 持有期与延续/反转的 `150` 个预先定义 long-only `0X/1.5X`
配置后，开发期合格数为 `0/150`。最佳 `90` 事件、`1σ`、`12×4h` 的反转规则在 Research 领先
`35.35pp`，Validation 却落后 `409.27pp`；OOS 未读取。详见
[funding-event screen](../../btc_funding_event_matched_benchmark/2026-09-03/README.md)。

现金现货-永续 carry 也无法击败现货 B&H。补齐并规范化 2020-01 至 2026-07 的 Binance
BTCUSDT 15m 现货归档后，测试 basis `0/5/10/20bps` 与已结算 Funding `0/0.25/0.5/1bps`
的 `16` 个无借币、等数量双腿组合；Research/Validation 开发期合格数为 `0/16`。最佳配置在
Research 落后 `140.51pp`、Validation 落后 `466.59pp`，虽最大总名义杠杆约 `1.10X`，仍不具备
收益优势。缺失的 16 段永续数据被强制平仓并单独记录，详见
[cash-and-carry screen](../../btc_cash_carry/2026-09-03/README.md)。

## Drawdown Guard Challenger

在不改变 SMA11/40 熊市空仓规则的前提下，`look180-dd20%-guard0.75X` 在价格较过去 180 个
完成日线高点回撤至少 20% 时，把非熊市暴露从 `1.5X` 降至 `0.75X`。该参数只按
Research/Validation 的默认及 `20+10 bps` 成本选择，OOS 未参与。默认成本下，
Research/Validation/OOS 超额为 `+218.42/+43.70/+33.97pp`，Full DD `-66.19%`，较基线低
约 `9.4pp`；中度成本三段仍为正。90/180/365 日 Bootstrap P05 为 `+2.30/+6.00/+7.30%`，但
7/30 日 P05 仍负，50+25 bps Validation 落后 `73.38pp`。这是一项风险 challenger，而不是
替代冻结基线的理由。

保护层家族的年度 walk-forward 更保守：每年用此前数据选择保护参数后，2020–2026 仅 4/7 年
超过 B&H，连续策略 `+1623.38%` 对 B&H `+976.30%`，DD `-68.96%`。因此低回撤的静态
候选不能据此升级为冻结版本。

## Daily-Execution Research Lead

跨 Binance 现货（2017–2019）与 USD-M（2020–最新）的日线 SMA10/40 迟滞版本（2 日入熊、
1 日恢复、1.5X active）在四个历史分段均超过 B&H，峰值日线内有效杠杆 `2.456X`。其 90/180/
365/730 日区块 Bootstrap 年化超额 P05 为 `+2.33%/+7.05%/+8.84%/+9.29%`；7/30 日仍为
`-2.41%/-0.64%`。这支持中周期防守机制，却不能证明短期路径稳定，也不能替代严格 15m 对
Funding 时点和盘中风险的审计。

## Decision

- 历史/留出结果：存在可复现的 BTC B&H 超额候选。
- 统计结论：中周期日线机制证据较强，但尚未证明短期路径稳健 Edge；高成本或局部市场阶段仍可能落后 B&H。
- 杠杆：`2.996X` 的候选几乎触及上限，实盘需要低于 3X 的开仓缓冲。
- 状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。

详细逐笔回放见 [enter3 审计](../../btc_sma10_enter3_active150_strict/2026-09-03/README.md)，
收益优先候选见 [SMA11 审计](../../btc_sma11_active160_strict/2026-09-03/README.md)。
混合严格执行的主报告见 [SMA11 strict-15m 审计](../../btc_sma11_enter2_active150_hybrid/2026-09-03/README.md)。
年度 walk-forward 见 [walk-forward report](../../btc_stitched_strict15m_walk_forward/2026-09-03/README.md)。
回撤保护完整报告见 [drawdown guard](../../btc_stitched_strict15m_drawdown_guard/2026-09-03/README.md)。
保护层年度 walk-forward 见 [guard walk-forward](../../btc_stitched_strict15m_guard_walk_forward/2026-09-03/README.md)。
跨交易所信号复核见 [Coinbase sanity audit](../../btc_sma11_coinbase_sanity/2026-09-03/README.md)。
尾部集中度审计见 [frozen-candidate tail audit](../../btc_sma11_enter2_active150_hybrid/2026-09-03/tail-audit/README.md)。
严格 3X 暴露边界见 [exposure boundary audit](../../btc_sma11_exposure_boundary/2026-09-03/README.md)。
成本断裂审计见 [cost break-even audit](../../btc_sma11_enter2_active150_hybrid/2026-09-03/cost-break-even/README.md)。
同风险杠杆基准审计见 [levered benchmark audit](../../btc_sma11_enter2_active150_hybrid/2026-09-03/levered-benchmark/README.md)。
全家族同风险基准筛选见 [matched-benchmark grid](../../btc_sma_matched_benchmark_grid/2026-09-03/README.md)。
独立突破家族审计见 [Donchian matched-benchmark screen](../../btc_donchian_matched_benchmark/2026-09-03/README.md)。
独立均值回归审计见 [mean-reversion matched-benchmark screen](../../btc_mean_reversion_matched_benchmark/2026-09-03/README.md)。
