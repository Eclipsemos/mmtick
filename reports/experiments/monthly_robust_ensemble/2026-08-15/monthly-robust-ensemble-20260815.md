# monthly-robust-ensemble-20260815

Development-selected monthly-stability ensemble of the frozen state strategy,
predefined MACD sleeves, and development-eligible BTC order flow.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.

## Search Result

- Components: `580`.
- Development portfolios: `280`.
- Development risk-eligible configurations: `725`.
- Best reused-confirmation coverage: `5/7`.
- Strict base-and-stress 7/7 configurations: `0`.

No development-selected monthly-stability ensemble reached +15% in all seven complete 2026 months under both base and stress costs.
Partial `2026-08` is excluded from strict counts.

## Development Selection

Weights `frozen_state=13%, tick_rule_imbalance_revert-window-42-long_only-threshold-0p75-ema-4-hold-6-cooldown-0-confirm-2=38%, flow-pair-reported_imbalance_follow-window-126-long_only-threshold-1p25-ema-4-hold-6-cooldown-0-confirm-2-weight0.25-reported_imbalance_follow-window-126-long_short-threshold-1p25-ema-4-hold-6-cooldown-6-confirm-1=34%, tick_rule_absorption-window-42-long_only-threshold-1p25-ema-4-hold-1-cooldown-6-confirm-1=15%`; leverage `8.00x`; monthly loss/profit locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 38.01% | -23.02% |
| 2026-02 | 52.09% | 48.69% |
| 2026-03 | 31.29% | 28.51% |
| 2026-04 | 25.05% | 22.11% |
| 2026-05 | -21.65% | -26.78% |
| 2026-06 | -4.83% | -10.55% |
| 2026-07 | -5.61% | -11.20% |
| 2026-08 | 5.93% | 4.16% |

## Best Confirmation Diagnostic

Weights `frozen_state=19%, reported_imbalance_follow-window-126-long_only-threshold-1p25-ema-4-hold-6-cooldown-6-confirm-2=56%, tick_rule_imbalance_revert-window-42-long_only-threshold-0p75-ema-4-hold-6-cooldown-0-confirm-2=25%`; leverage `8.00x`; monthly loss/profit locks `25%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 17.74% | 51.32% |
| 2026-02 | 77.02% | 75.49% |
| 2026-03 | 50.31% | 46.48% |
| 2026-04 | 19.11% | 17.26% |
| 2026-05 | -28.22% | -33.78% |
| 2026-06 | 17.58% | 15.63% |
| 2026-07 | -14.70% | -19.64% |
| 2026-08 | 5.22% | 3.21% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- Order-flow history restricts the common development period to 2024/2025.
- The ensemble search was specified after observing prior 2026 failures.
- Static sleeve rebalancing turnover beyond embedded component costs is not modeled.
- Drawdown is daily-close only; liquidation and borrowing costs are not modeled.
