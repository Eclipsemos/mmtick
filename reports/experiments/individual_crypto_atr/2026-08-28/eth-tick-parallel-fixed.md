# ETHUSDT 独立 Tick ATR 复核

生成时间：`2026-08-28T14:03:13.224327+00:00`

该复核不读取或修改冻结的 BTC/ETH 组合策略。成交使用 250 ms 聚合 tick，并计入手续费、滑点和历史资金费。

## 选择结果

开发和验证段选出的参数为 ATR(21) x 3。

| 区间 | 收益 | 最大回撤 | 交易数 | 胜率 | PF | 手续费 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024开发 | -60.63% | -67.59% | 855 | 34.04% | 0.83 | $61,260.18 |
| 2025验证 | -59.56% | -73.25% | 922 | 36.33% | 0.88 | $71,935.92 |
| 2026-08-21前确认 | -47.28% | -55.13% | 589 | 31.07% | 0.83 | $48,411.98 |
| 2026-08-22后Forward | -9.81% | -10.22% | 16 | 25.00% | 0.14 | $1,521.11 |

## 判定

状态：`rejected`。

失败门槛：`all_pre_forward_splits_positive`, `drawdown_controlled`, `confirmation_profit_factor`。

The development-selected candidate failed: all_pre_forward_splits_positive, drawdown_controlled, confirmation_profit_factor.

Forward 结果不参与参数选择；本报告不批准模拟盘或实盘。
