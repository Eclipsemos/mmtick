# SOXLUSDT Full Listing-to-Date Current Strategy Replay

Generated: 2026-08-07T13:15:12.373890+00:00

Data coverage: 2026-05-15T14:00:09.705000+00:00 to 2026-08-07T13:13:15.531000+00:00. The first 200 x 15m bars are indicator warm-up; measured performance starts at 2026-05-17T16:00:00+00:00.

## Warehouse

- 9,973,473 stored 250ms Tick buckets representing 184,237,987 underlying Binance trade IDs in the measured range.
- 8,060 closed 15-minute klines and 252 funding-rate events.
- SQLite integrity check: `ok`. Archive-to-REST raw trade IDs are continuous.

## Current strategy

ATR(21) x 4, 8-bar trend efficiency >= 0.25, 0.25 ATR reversal confirmation, 2.0 ATR profit activation, 0.5 ATR profit trail, and 1.4 ATR continuation re-entry. Execution uses 2x leverage, 62.5% position budget, 5 bps fee and 2 bps slippage.

## Result

| Metric | Value |
|---|---:|
| Final equity | 41,692.75 USDT |
| Net profit | -58,307.25 USDT |
| Net return | -58.31% |
| Maximum drawdown | -75.07% |
| Completed round trips | 191 |
| Win rate | 59.69% |
| Profit factor | 0.78 |
| Fees | 15,043.45 USDT |
| Funding | -780.31 USDT |
| Profit exits | 114 |
| Continuation re-entries | 32 |
| Ending position | FLAT |

This is a historical replay under the repository execution model. It does not model order-book depth, API latency, liquidation, exchange rejection, or service outages and is not a forecast.
