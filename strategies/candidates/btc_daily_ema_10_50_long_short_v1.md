# BTC Daily EMA(10,50) Long/Short v1

Status: `provisional_forward_candidate`. This candidate is not rejected under the revised research
threshold, but it is not approved for paper or live trading.

## Frozen Definition

| Item | Value |
|---|---:|
| Instrument | BTCUSDT USD-M perpetual |
| Bars | 1 day, UTC |
| Direction | Long when EMA(10) > EMA(50), short when EMA(10) < EMA(50) |
| Signal/fill | Closed daily bar / next daily open |
| Exposure | 1.0x equity |
| Costs | 5 bps fee + 2 bps slippage per fill, with funding |
| Evidence lock | 2026-08-10 UTC |
| Forward evidence starts | 2026-08-11 UTC |

The machine-readable definition and parameter hash are in
[btc_daily_ema_10_50_long_short_v1.json](btc_daily_ema_10_50_long_short_v1.json).

## Acceptance Evidence

| Split | Return | Max drawdown | Trades | Profit factor |
|---|---:|---:|---:|---:|
| 2024-02-01 to 2024-12-31 training | +3.37% | -50.23% | 9 | 1.08 |
| 2025 validation | +11.85% | -35.38% | 8 | 1.39 |
| 2026-01-01 to 2026-08-10 confirmation | +15.61% | -15.61% | 5 | 2.06 |

The revised threshold requires at least one confirmation month above 15%. June 2026 returned
`+20.65%`, while the full confirmation split remained positive. The parameters were part of the
original 72-candidate family comparison and were not chosen using the confirmation ranking.

## Limits

The training drawdown reached `-50.23%`, and confirmation contains only five completed trades.
Therefore the candidate is eligible only for frozen forward monitoring. Exposure above 1.0x,
parameter changes, or use in a trading process are not authorized. A production review requires at
least 365 new complete UTC days and eight completed forward trades. A forward drawdown at or below
`-25%` rejects the candidate.

Run the deterministic monitor after updating complete daily data:

```bash
.venv/bin/python scripts/evaluate_btc_ema_forward.py
```
