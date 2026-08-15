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

The selected factor follows an extreme BTC top-position/global-account ratio spread and is
long-only. It receives 25% of the hybrid allocation; the frozen four-factor anchor receives 75%,
with 1.5x outer leverage.

| Split | Return | Max DD | 25% months |
|---|---:|---:|---:|
| 2021-2023 discovery | +1593.00% | -34.04% | 7/36 |
| 2024-2025 validation | +262.69% | -34.59% | 5/24 |
| 2026 reused confirmation | +207.03% | -19.19% | 3/8 |
| 2026 stress 10+5 bps | +172.43% | -21.72% | 3/8 |

The new data source is informative but does not fill a fourth +25% confirmation month. Decision:
`rejected_after_confirmation`. Approved for trading: `false`.

The same development protocol was rerun after the objective was lowered to `+15%` per month. The
selected parameters did not change: only January, February, and June reached +15% in the reused
2026 confirmation (3/8 months). Its base-cost compounded monthly rate was about 14.02%, and the
10+5 bps stress rate was about 12.53%, so this is not a verified every-month +15% solution.

## Reproduce

```bash
.venv/bin/python scripts/update_futures_metrics.py --start 2021-01-01
.venv/bin/python scripts/mine_market_metric_factors.py
```

Raw archives are stored under `data/futures_metrics/` and excluded from Git. The timestamped JSON
artifact contains the complete daily/monthly evidence; the adjacent Markdown file summarizes it.
