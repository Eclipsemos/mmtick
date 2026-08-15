# order-flow-complement-20260815

BTC order-flow complement search for the strict monthly target.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.

## Search Result

- Order-flow candidates: `1280`.
- Independent development-eligible factors: `22`.
- Development risk-eligible configurations: `1669`.
- Best reused-confirmation coverage: `5/7`.
- Strict base-and-stress 7/7 configurations: `0`.

No development-selected order-flow complement reached +15% in all seven complete 2026 months under both base and stress costs.
Partial `2026-08` is excluded from strict counts.

## Single-Factor Development Selection

`tick_rule_imbalance_revert-window-42-long_only-threshold-0p75-ema-4-hold-6-cooldown-0-confirm-2`; state/flow `40%/60%`; leverage `6.00x`; monthly loss/profit locks `20%/18%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | -28.11% | -26.87% |
| 2026-02 | 29.16% | 25.43% |
| 2026-03 | 52.79% | 47.65% |
| 2026-04 | 43.07% | 40.74% |
| 2026-05 | -29.70% | -22.46% |
| 2026-06 | 20.96% | 17.89% |
| 2026-07 | -21.20% | -26.21% |

## Best Single-Factor Confirmation Diagnostic

`tick_rule_active_pressure-window-42-long_only-threshold-1p25-ema-4-hold-6-cooldown-0-confirm-2`; state/flow `50%/50%`; leverage `6.00x`; monthly loss/profit locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 18.66% | 15.31% |
| 2026-02 | 32.75% | 28.56% |
| 2026-03 | 58.97% | 52.91% |
| 2026-04 | 32.44% | 29.55% |
| 2026-05 | -37.00% | -24.99% |
| 2026-06 | 35.49% | 30.11% |
| 2026-07 | 16.41% | -24.58% |

## Two-Factor Development Selection

`flow-pair-reported_imbalance_follow-window-126-long_only-threshold-1p25-ema-4-hold-6-cooldown-0-confirm-2-weight0.25-reported_imbalance_follow-window-126-long_short-threshold-1p25-ema-4-hold-6-cooldown-6-confirm-1`; state/flow `40%/60%`; leverage `6.00x`; monthly loss/profit locks `15%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 20.42% | 16.79% |
| 2026-02 | 25.20% | 21.10% |
| 2026-03 | 64.66% | 59.39% |
| 2026-04 | 37.31% | 34.60% |
| 2026-05 | -16.40% | -21.48% |
| 2026-06 | 18.60% | 23.82% |
| 2026-07 | -20.53% | -25.05% |

## Best Two-Factor Confirmation Diagnostic

`flow-pair-reported_imbalance_follow-window-126-long_only-threshold-1p25-ema-4-hold-6-cooldown-0-confirm-2-weight0.25-reported_imbalance_follow-window-126-long_short-threshold-1p25-ema-4-hold-6-cooldown-6-confirm-1`; state/flow `40%/60%`; leverage `6.00x`; monthly loss/profit locks `15%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 20.42% | 16.79% |
| 2026-02 | 25.20% | 21.10% |
| 2026-03 | 64.66% | 59.39% |
| 2026-04 | 37.31% | 34.60% |
| 2026-05 | -16.40% | -21.48% |
| 2026-06 | 18.60% | 23.82% |
| 2026-07 | -20.53% | -25.05% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- Order-flow archives provide only two complete development years.
- The order-flow complement was studied after observing prior 2026 failures.
- Reported buyer/seller direction is incomplete; tick-rule features are separate.
- Drawdown is daily-close only; liquidation and borrowing costs are not modeled.
