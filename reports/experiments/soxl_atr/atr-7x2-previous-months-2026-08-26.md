# SOXLUSDT ATR(7) x 2.0 Previous-Month Review

## Method

`ATR(7) x 2.0` was fixed before evaluating each period. The replay uses the
SOXLUSDT live strategy structure: 15-minute tick execution, long-only, trend
efficiency 8 / 0.25, 0.25 ATR reversal confirmation, no profit protection, no
continuation re-entry, 2x isolated leverage, 62.5% position allocation, 5 bps
fees, 2 bps slippage, and historical funding. Live startup alignment is disabled.
Each period has its own 200-bar warmup. The frozen `ATR(32) x 3.0` is the control.

## Monthly Results

| Period | ATR(7) x 2.0 | ATR(32) x 3.0 | ATR(7) DD | ATR(32) DD | ATR(7) trades | ATR(32) trades |
|---|---:|---:|---:|---:|---:|---:|
| 2026-05-17 to 05-31 | +0.04% | +33.32% | -11.86% | -14.66% | 31 | 26 |
| 2026-06 | -22.52% | +36.41% | -40.12% | -20.65% | 67 | 46 |
| 2026-07 | -17.15% | +26.43% | -32.48% | -26.71% | 60 | 42 |
| 2026-08-09 to 08-26 | +6.59% | -5.23% | -11.49% | -13.87% | 32 | 25 |

`ATR(7) x 2.0` had PF `1.00` in May, `0.70` in June, `0.60` in July, and `1.36`
in the current forward window. The current strategy had PF `2.02`, `1.42`,
`0.92`, and `0.70` in the same periods.

## Full Listing-to-Date Context

From the common post-warmup start on `2026-05-17` through `2026-08-26`, the
proposed setting returned `-24.81%` with a `-51.40%` maximum drawdown. The frozen
setting returned `+157.63%` with a `-26.71%` maximum drawdown. This full replay is
not a fresh parameter-selection holdout; it is context for phase stability.

## Assessment

The 8 August-to-date result for `ATR(7) x 2.0` is not representative of its prior
months. It failed in both June and July, with materially larger drawdowns and more
turnover. The positive recent result is therefore a regime-specific observation,
not a robust replacement candidate.

Keep `ATR(32) x 3.0` frozen. Continue observing `ATR(7) x 2.0` as a shadow
candidate only; do not deploy it or increase leverage based on this comparison.

Machine-readable outputs:

- `atr-7x2-previous-months-2026-08-26.json`
- `atr-7x2-full-2026-08-26.json`
