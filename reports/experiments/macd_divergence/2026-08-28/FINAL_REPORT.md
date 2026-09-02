# 币圈半神 MACD 背离策略：阶段性验证报告

## 结论

**Strategy Status: REJECTED**（当前数据、成本和协议下不进入 Paper/Real Money）。

这不是因为某个参数点表现不好，而是因为开发期、验证期和 OOS 的多数结果都不稳定；15m BTC 的开发冻结候选 OOS 平均 `-0.222R`、PF `0.737`，ETH 为 `-0.087R`、PF `0.872`。BTC 的 OOS 平均 R bootstrap 95% 区间为 `[-0.371, -0.067]`，平均 R 为正概率 `0.22%`；ETH 区间为 `[-0.216, 0.044]`，为正概率 `9.72%`。

## 数据与协议

- Binance USD-M Futures，BTCUSDT/ETHUSDT；共同可用历史从 `2020-01-01` 开始，不能声称覆盖 2017。
- 15m 档案：每个品种 `233,455` 根，至 `2026-08-28 19:44 UTC`，间隔缺口 `0`。
- 5m 档案：每个品种 `700,374` 根，聚合出 5m/30m/1h/4h。
- MACD `13/34/9`，Wilder ATR `13`；默认 ATR 止损 `1.0`、RR `2.0`（网格测试 `0.5..2.0` 与 `1..4`）。
- Confirmed Pivot 只在右侧 K 线完成后确认；Rolling Extremum 只比较历史窗口；Histogram 匹配使用 Swing 当根值。
- 背离确认后等待之后第一次 Histogram 收缩，下一根 Open 成交；同柱 SL/TP 按 Stop 优先并计数。
- 每笔风险 `1%`，名义杠杆上限 `5x`，手续费 `4 bps/边`，滑点 `2 bps/边`。
- 所有信号、指标、止损、止盈只使用当时已完成 K 线；指标预热保留在完整历史，分区资金曲线独立起算。

## Funding 复核

已下载 Binance Funding 事件：每个品种 7,297 次，覆盖 2020-01 至最新。对于 Binance 返回空 `markPrice` 的 4,198 次事件，使用事件所在已完成 K 线收盘价作为透明 fallback。Funding 在包含事件的柱开始、柱内止损检查之前结算。

Funding 不改变拒绝结论：15m BTC OOS 从无 Funding 的 `-68.67%` 变为 `-68.69%`，ETH 从 `-43.18%` 变为 `-43.02%`。唯一正向的 BTC 4h OOS 从 `+4.52%` 变为 `+4.84%`，PF `1.248`，但仍只有 24 笔交易；ETH 4h 仍为 `-4.49%`、PF `0.859`。详细对照见 `funding/README.md`。

## 15m 冻结候选

| 标的 | 冻结候选（只按 Research+Validation 选择） | Research 平均 R | Validation 平均 R | OOS 平均 R | OOS PF | OOS 交易 | OOS 收益 | OOS 最大回撤 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | Pivot Double, ATR 1.5, RR 3 | -0.121 | -0.130 | -0.222 | 0.737 | 498 | -68.67% | -70.49% |
| ETHUSDT | Rolling-20 Double, ATR 2, RR 2.5 | -0.056 | -0.127 | -0.087 | 0.872 | 571 | -43.18% | -54.16% |

### 成本敏感性

BTC 15m 冻结候选在无摩擦假设下 OOS 平均约 `+0.020R`、PF `1.010`，加入仅 `1 bps` 滑点后即变为 `-0.199R`、PF `0.759`。ETH 无摩擦仍为 `-0.004R`、PF `0.977`。因此 BTC 的微弱无摩擦结果不能视为可交易 Edge。

### Double vs Triple 与 Swing 方法

15m 中，Triple 没有跨两个开发分区形成稳定正优势；BTC OOS 最佳 Triple 诊断约 `-0.150R`，ETH 约 `-0.050R`。Rolling 通常优于 Pivot，但仍未达到成本后正期望。过滤器逐个测试（EMA200、RSI、ATR 分位、Volume、Histogram/ATR）也没有在 Research 与 Validation 同时提供可迁移的正优势。

## 多周期 OOS

| 标的 | 周期 | Research R | Validation R | OOS R | OOS PF | OOS 交易 | 判读 |
|---|---:|---:|---:|---:|---:|---:|---|
| BTCUSDT | 5m | -0.206 | -0.264 | -0.323 | 0.663 | 2,755 | 明确拒绝 |
| BTCUSDT | 30m | -0.028 | -0.010 | -0.049 | 0.916 | 198 | 接近零但为负 |
| BTCUSDT | 1h | 0.390 | 0.370 | -0.326 | 0.639 | 31 | 明显失效，样本少 |
| BTCUSDT | 4h | 0.193 | 0.213 | 0.207 | 1.228 | 24 | 值得独立复核，样本不足 |
| ETHUSDT | 5m | -0.170 | -0.187 | -0.137 | 0.836 | 1,084 | 明确拒绝 |
| ETHUSDT | 30m | -0.024 | -0.010 | 0.020 | 0.999 | 266 | 统计上等于零 |
| ETHUSDT | 1h | 0.004 | 0.015 | -0.026 | 0.951 | 165 | 轻微为负 |
| ETHUSDT | 4h | 0.232 | 0.203 | -0.106 | 0.857 | 37 | 失效 |

BTC 4h 是唯一三段均为正的周期/品种组合，但 OOS 仅 24 笔，不能据此批准交易；ETH 4h 在 OOS 反转为负，说明该现象未跨资产稳定。

## 工程验证

- `tests/test_macd_divergence.py`：`18 passed`。
- `.venv/bin/ruff check`：核心模块、运行器、下载器和测试全部通过。
- 覆盖 EMA/MACD、Wilder ATR、Pivot 确认时点、Rolling 因果性、Double/Triple、入场延迟、下一根 Open、SL/TP、同柱 Stop 优先、杠杆上限、重复信号、分区回放、RSI/Volume 过滤和 Monte Carlo ruin 路径统计。

## 参数邻域稳健性

冻结候选是在 OOS 揭示前确定的；本节只审计 MACD、ATR 周期、止损/RR 和 Histogram 匹配邻域，未用 OOS 重新挑选参数。每个品种包含 25 个局部组合，并额外运行规格要求的完整 `6 × 6 × 4 = 144` 个 MACD 组合：

- BTC：局部组合 Research 与 Validation 同时为正 `0/25`、OOS 为正 `0/25`；Histogram 使用确认窗口的 OOS 平均 R 为 `-0.195`，仍为负。完整 MACD 网格 Research/Validation 同时为正 `0/144`、OOS 为正 `0/144`，OOS 平均 R 范围 `-0.393` 至 `-0.147`。
- ETH：局部组合 Research 与 Validation 同时为正 `0/25`、OOS 为正 `0/25`；Histogram 使用确认窗口的 OOS 平均 R 为 `-0.078`，仍为负。完整 MACD 网格 Research/Validation 同时为正 `0/144`、OOS 为正 `3/144`，OOS 平均 R 范围 `-0.136` 至 `0.018`。这 3 个 OOS 正值组合在开发期没有同时为正，属于事后挖掘，不能作为候选。

因此没有观察到围绕冻结点的稳定正值平台；结果不是单个参数点异常导致的。完整逐组合表见 [`robustness/README.md`](robustness/README.md) 和 [`robustness/results.json`](robustness/results.json)。

## 可视化审计

图表由冻结配置重新回放生成，随机种子固定为 `20260828`。权益/回撤曲线和每个品种随机抽取的 10 笔盈利、10 笔亏损案例（蜡烛、Histogram、P 点、Entry、SL、TP）如下：

- [BTCUSDT equity/drawdown](plots/btcusdt-equity-drawdown.svg) · [BTCUSDT curve CSV](plots/btcusdt-equity-drawdown.csv) · [BTCUSDT trade cases](plots/btcusdt-trade-cases.svg)
- [ETHUSDT equity/drawdown](plots/ethusdt-equity-drawdown.svg) · [ETHUSDT curve CSV](plots/ethusdt-equity-drawdown.csv) · [ETHUSDT trade cases](plots/ethusdt-trade-cases.svg)

## 4h 低周期路径复核

4h 候选进一步用同一时期已完成 5m K 线扫描退出路径，保持 4h 信号、下一根 Open 入场、仓位、费用和 Funding 不变。BTC（`rolling-20-3point-at_swing-atr1.25-rr4`）和 ETH（`rolling-10-3point-at_swing-atr0.5-rr4`）在 Research、Validation、OOS、Full 四个分区的交易数、退出原因、收益和平均 R 均与 4h OHLC 回放一致；标准和 5m 路径歧义柱均为 `0`。因此 4h 正负结果不是由同柱 OHLC 路径假设造成的。完整对照见 [`path_confirmation/README.md`](path_confirmation/README.md)。

## 决策矩阵

| 问题 | BTCUSDT 15m | ETHUSDT 15m | 判断 |
|---|---:|---:|---|
| 是否有正 Expectancy？ | `-0.222R` | `-0.087R` | 否 |
| OOS Profit Factor | `0.737` | `0.872` | 均低于 1 |
| Bootstrap 平均 R 为正概率 | `0.22%` | `9.72%` | 无统计支持 |
| OOS 最大回撤 | `-70.49%` | `-54.16%` | 超过可接受风险 |
| 最长连续亏损 | `14` | `14` | 风险显著 |
| 费用/滑点敏感性 | 1 bps 后转负 | 无摩擦仍约零 | 不可交易 |
| 跨周期/跨资产稳定性 | BTC 4h 仅 24 笔为正 | ETH 4h OOS 负 | 不稳定 |

结论：当前 Edge 不具备跨样本、资产或周期的统计稳定性；Triple 背离没有优于 Double 背离的可迁移优势；单过滤器没有改善 OOS；邻域中没有正值平台，参数存在事后选择风险。建议每笔风险不超过 `0.5%` 仅用于研究性 forward observation，不能进入 Paper Trading 或 Real Money。最终状态：**REJECTED**。

## 已知边界与下一步

当前数据仍没有 2017-2019 的 Binance USD-M 档案；Funding 中空 `markPrice` 使用事件所在已完成 K 线收盘价是近似。4h BTC 的三段正值仅有 24 笔 OOS 交易，仍需独立扩大样本和前瞻观察；在此之前不应把它升级为 Paper Trading Candidate。所有新增图表、邻域和路径结果均为诊断用途，不改变 `REJECTED` 状态。

原始 JSON、候选全表、OOS 交易 CSV、多周期、稳健性和路径结果位于同目录：`results.json`、`btcusdt-selected-oos-trades.csv`、`ethusdt-selected-oos-trades.csv`、`timeframes/`、`robustness/` 和 `path_confirmation/`。

## 复现入口

```bash
PYTHONPATH=src:scripts/research python3 scripts/research/research_macd_divergence.py
PYTHONPATH=src:scripts/research python3 scripts/research/research_macd_divergence_robustness.py --full-macd-grid
PYTHONPATH=src:scripts/research python3 scripts/research/research_macd_divergence_funding.py
PYTHONPATH=src:scripts/research python3 scripts/research/research_macd_divergence_path.py
PYTHONPATH=src:scripts/research python3 scripts/research/plot_macd_divergence.py
```
