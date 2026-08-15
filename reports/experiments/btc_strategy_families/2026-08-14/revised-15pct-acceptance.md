# Revised 15% Single-Month Acceptance Review

Decision: `provisional_forward_candidate`, not rejected and not approved for trading.

The research target changed after the original family comparison from 25% geometric monthly return
to at least one confirmation month returning 15% or more. This review applies that revised rule to
the already-tested candidates; it performs no new parameter search.

## Candidate

`btc-daily-ema-10-50-long-short-v1` uses closed UTC daily bars, EMA(10/50) direction, next-day-open
fills, 1.0x exposure, 5 bps fees, 2 bps slippage, and historical funding.

| Gate | Required | Evidence | Pass |
|---|---:|---:|---|
| Positive training return | > 0% | +3.37% | yes |
| Positive validation return | > 0% | +11.85% | yes |
| Positive confirmation return | > 0% | +15.61% | yes |
| Best confirmation month | >= 15% | June 2026: +20.65% | yes |
| Confirmation profit factor | >= 1.5 | 2.06 | yes |
| Confirmation completed trades | >= 5 | 5 | yes |
| Confirmation max drawdown | > -20% | -15.61% | yes |

The candidate parameters were present in the original 72-candidate grid and were the EMA family
winner selected from training and validation. However, the revised acceptance threshold was chosen
after confirmation results were known. Therefore this is a provisional status change, not fresh
out-of-sample validation.

## Risk Limits

Training max drawdown was `-50.23%`; leverage above 1.0x is prohibited. The candidate remains
research-only until at least 365 new complete UTC days and eight completed forward trades exist.
A forward drawdown at or below `-25%` rejects it. Parameter changes require a new candidate ID and
new evidence lock.

The first frozen forward day, 2026-08-11, returned `+0.52%` after entry costs and funding while
ending short. One day is insufficient for approval, but it does not trigger a rejection gate.

Forward report:
[btc-daily-ema-10-50-long-short-v1-forward.md](../forward/btc-daily-ema-10-50-long-short-v1-forward.md).
