# Fresh Forward Observation: 2026-08-22+

## Data Boundary

The MMTICK warehouse was incrementally updated from Binance USD-M Futures public
klines and funding. BTCUSDT and ETHUSDT 15m data now end at `2026-08-25 18:30 UTC`;
the last complete H4 bar ends at `2026-08-25 16:00 UTC` (the current 16:00–20:00
bar was excluded as incomplete). The observation starts at `2026-08-22 09:00 UTC`,
after the previous research cutoff. No parameters were re-selected on this data.

## Fixed Candidates

The previously selected configurations were replayed with closed-H4 signals,
next-H4-open fills, realized funding, 14 bps normal costs, and 30 bps stress costs.

| Candidate | Forward | Stress | Trades | Assessment |
|---|---:|---:|---:|---|
| ETH D1/H4 breakout, d2.5, h18 | 0.00% | 0.00% | 0 | No signal yet |
| ETH high-vol breakout, m0.75, h18 | 0.00% | 0.00% | 0 | No signal yet |
| BTC/ETH 50/50 selected portfolio | +1.00% | +0.84% | 2 legs | Too few observations |

The high-volatility ETH breakout with h24 generated one trade at `+0.96%` normal
and `+0.80%` under stress. BTC fade variants produced mixed one- or two-trade
outcomes, including losses. These are observations, not statistically meaningful
performance estimates.

## Decision

Keep all candidates in forward observation only. Do not update parameters, freeze a
new strategy, or connect to paper/live execution. Accumulate at least several weeks
of complete H4 data and a materially larger trade sample before evaluating promotion.

Detailed machine-readable output:
`btc-eth-support-resistance-forward-20260822.json`.
