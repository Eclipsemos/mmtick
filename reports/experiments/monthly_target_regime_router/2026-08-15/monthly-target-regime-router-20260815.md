# monthly-target-regime-router-20260815

Persistent-MACD diversification audit for the frozen causal market-state strategy.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.

## Strict Result

| Family | Development eligible | Best base/stress complete months | Strict 7/7 |
|---|---:|---:|---:|
| Fixed mixture | 736 | 4/7 | 0 |
| Causal regime route | 828 | 4/7 | 0 |

No development-eligible fixed mixture or causal regime route reached +15% in all seven complete 2026 months under both base and stress costs.
Partial `2026-08` is excluded from every strict count.

## Frozen Baseline

| Split | Return | Max DD | 15% months |
|---|---:|---:|---:|
| discovery | 2001.56% | -33.87% | 13/36 |
| validation | 140.30% | -27.87% | 6/24 |
| confirmation | 182.94% | -28.19% | 4/8 |

## Fixed Mixture

**Development-selected:** `eth_perp-macd-1440m-16-48-5-long_short-confirm3`, outer leverage `2.00x`, state weight `75.00%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 86.86% | 77.76% |
| 2026-02 | 79.52% | 77.41% |
| 2026-03 | 22.20% | 19.42% |
| 2026-04 | 8.80% | 7.74% |
| 2026-05 | -24.79% | -28.40% |
| 2026-06 | 27.85% | 24.68% |
| 2026-07 | -5.75% | -7.05% |
**Best reused-confirmation diagnostic:** `eth_perp-macd-1440m-16-48-5-long_short-confirm3`, outer leverage `2.00x`, state weight `75.00%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 86.86% | 77.76% |
| 2026-02 | 79.52% | 77.41% |
| 2026-03 | 22.20% | 19.42% |
| 2026-04 | 8.80% | 7.74% |
| 2026-05 | -24.79% | -28.40% |
| 2026-06 | 27.85% | 24.68% |
| 2026-07 | -5.75% | -7.05% |

## Causal Regime Route

**Development-selected:** `eth_perp-macd-1440m-5-15-14-long_short-confirm3`, outer leverage `1.50x`, active trend weight `25.00%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 61.86% | 53.47% |
| 2026-02 | 76.19% | 72.90% |
| 2026-03 | 27.62% | 24.30% |
| 2026-04 | 10.02% | 8.35% |
| 2026-05 | -25.32% | -29.07% |
| 2026-06 | 31.74% | 27.89% |
| 2026-07 | -5.07% | -7.05% |
**Best reused-confirmation diagnostic:** `eth_perp-macd-1440m-5-15-14-long_short-confirm3`, outer leverage `1.50x`, active trend weight `25.00%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 61.86% | 53.47% |
| 2026-02 | 76.19% | 72.90% |
| 2026-03 | 27.62% | 24.30% |
| 2026-04 | 10.02% | 8.35% |
| 2026-05 | -25.32% | -29.07% |
| 2026-06 | 31.74% | 27.89% |
| 2026-07 | -5.07% | -7.05% |

## Limitations

- 2026 has been repeatedly inspected and is reused confirmation evidence, not a fresh holdout.
- The frozen market-state baseline came from prior research using overlapping data.
- Routing is selected from the persistent MACD state known by the prior UTC day close.
- Daily sleeve returns approximate strategy switching; route transitions incur explicit cost.
- Drawdown is measured at daily closes; liquidation and borrowing costs are not modeled.
