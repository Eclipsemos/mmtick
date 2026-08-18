# Futures Market-Metric Factor

This research-only experiment adds Binance USD-M futures market metrics to the BTC/ETH factor
universe. Inputs include open interest, taker long/short volume, global account positioning, and
top-trader account/position ratios. Daily 5m archives are reduced to the last complete snapshot
and average taker imbalance in each closed 4h bar. Incomplete source rows are skipped, never
zero-filled.

Signals use a trailing 30/90/180-day normalization, are computed after a 4h bar closes, and fill
at the next 4h open. The search uses 2021-2023 discovery and 2024-2025 validation. The 2026 interval
is reused confirmation only and does not choose the metric, threshold, allocation, or leverage.

## Result

The current `+15%` protocol requires both development segments to be profitable, remain above
`-35%` drawdown, and reach the monthly target in at least 15% of their months. The selected factor
fades extreme ETH top-trader position crowding and is long-only. It receives 60% of the hybrid
allocation; the frozen four-factor anchor receives 40%, with 2.5x outer leverage.

| Split | Return | Max DD | 15% months |
|---|---:|---:|---:|
| 2021-2023 discovery | +1426.61% | -34.22% | 12/36 |
| 2024-2025 validation | +249.74% | -33.83% | 7/24 |
| 2026 reused confirmation | +159.81% | -20.90% | 3/8 |
| 2026 stress 10+5 bps | +124.15% | -24.64% | 3/8 |

Only January, February, and June reached +15% in reused 2026 confirmation. March, April, and
August were positive but below target; May and July lost money. The result therefore misses the
required 4/8 confirmation months under both base and stress costs. Decision:
`rejected_after_confirmation`. Approved for trading: `false`.

The final artifact is `market-metric-factor-20260815-135357-577775`. The protocol was also tested
with a stricter 25% development consistency gate, which produced no eligible hybrid. These
neighborhood checks do not change the rejection or make 2026 a fresh holdout.

## Reproduce

```bash
.venv/bin/python scripts/maintenance/update_futures_metrics.py --start 2021-01-01
.venv/bin/python scripts/research/mine_market_metric_factors.py
```

Raw archives are stored under `data/futures_metrics/` and excluded from Git. The timestamped JSON
artifact contains the complete daily/monthly evidence; the adjacent Markdown file summarizes it.
