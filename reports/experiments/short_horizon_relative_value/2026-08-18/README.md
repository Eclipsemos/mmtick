# BTC/ETH Short-Horizon Relative-Value Audit

Generated: 2026-08-17T17:28:36.651785+00:00

Decision: `rejected`. Paper/live approval: `false/false`.

This study fades the closed-bar BTC/ETH log-ratio z-score with equal notional legs, fills both legs at the next synchronized bar open, enforces a time exit, and charges fees, slippage, and historical funding. It is relative value, not risk-free arbitrage.

## Search

- Candidates: `324`; development eligible: `0`.
- Intervals: 15m, 1h, 4h; lookbacks: 1/3/7 days; maximum holds: 4/12/24 hours.
- Train: 2021-2023; validation: 2024-2025; reused confirmation: 2026 through August 11.
- Base cost: 5 bps fee + 2 bps slippage per leg per fill; stress: 10 + 5 bps.

## Development-Selected Result

Candidate: `relative-shock-continuation-240m-window7d-entry2.5-hold12h`.

| Split | Base return | Stress return | Base DD | Trades | Base PF |
|---|---:|---:|---:|---:|---:|
| train | 2.08% | -27.28% | -16.74% | 212 | 1.03 |
| validation | -15.00% | -33.18% | -18.79% | 150 | 0.74 |
| reused confirmation | -6.11% | -13.73% | -6.43% | 53 | 0.57 |

Zero-cost diagnostic for the same candidate: train `37.29%`, validation `4.90%`, reused confirmation `1.10%`. The gross signal is weak and does not survive two-leg execution costs.

## Decision

No development-robust short-horizon pair survived reused confirmation.

The historical warehouse has no bid/ask or order-book depth. A positive bar replay would only justify a forward recorder that measures synchronized executable spread, two-leg latency, partial fills, and adverse selection. It would not justify trading.

## Limitations

- This is relative-value speculation, not locked cash-and-carry arbitrage.
- OHLCV next-open fills cannot model two-leg latency, bid/ask spread, depth, or leg risk.
- The equal-notional hedge does not guarantee beta neutrality.
- 2026 has been viewed in earlier studies and is reused confirmation, not a fresh holdout.
