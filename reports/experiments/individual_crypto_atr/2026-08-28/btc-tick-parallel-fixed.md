# BTCUSDT 独立 Tick ATR 复核

生成时间：`2026-08-28T19:25:21.706002+00:00`

该复核不读取或修改冻结的 BTC/ETH 组合策略。成交使用 250 ms 聚合 tick，并计入手续费、滑点和历史资金费。

## 选择结果

开发和验证段选出的参数为 ATR(14) x 3。

| 区间 | 收益 | 最大回撤 | 交易数 | 胜率 | PF | 手续费 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024开发 | -77.21% | -78.09% | 869 | 28.54% | 0.69 | $46,296.03 |
| 2025验证 | -70.11% | -70.78% | 945 | 30.69% | 0.72 | $57,824.73 |
| 2026-08-21前确认 | -70.68% | -71.66% | 613 | 27.41% | 0.59 | $36,426.02 |
| 2026-08-22后Forward | -6.76% | -7.29% | 18 | 27.78% | 0.24 | $1,744.86 |

## 判定

状态：`rejected`。

失败门槛：`all_pre_forward_splits_positive`, `drawdown_controlled`, `confirmation_profit_factor`。

The development-selected candidate failed: all_pre_forward_splits_positive, drawdown_controlled, confirmation_profit_factor.

Forward 结果不参与参数选择；本报告不批准模拟盘或实盘。
