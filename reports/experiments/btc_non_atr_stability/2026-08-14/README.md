# BTCUSDT Non-ATR Stability Audit

Generated: 2026-08-14T12:49:08.103078+00:00

This audit compares common non-ATR signal families with closed-bar signals and next-bar-open fills. All base results use 5 bps fee and 2 bps slippage per fill, plus historical funding. It does not model liquidation.

Data: 2024-01-01T00:00:00+00:00 through 2026-08-11T23:59:59.999000+00:00; 91,584 complete 15-minute bars.

## Families

- EMA trend and deadband
- MACD trend following
- Donchian breakout
- time-series momentum
- RSI mean reversion
- Bollinger-band mean reversion

## Stability Gates

- positive net return in each disjoint split
- at least six completed trades in confirmation
- maximum drawdown no worse than -25% in every split
- at least 55% positive calendar months in confirmation
- repeat all three splits at 10 bps fee plus 5 bps slippage per fill
- remain positive in each split with no drawdown worse than -25%

## Family Winners

Family winners are selected using training and validation only. Confirmation is held out from that selection, but this remains exploratory research because the strategy families and grids were evaluated on the archived dataset.

| Family | Candidate | Train | Validation | Confirmation | Confirm DD | Trades | Positive months |
|---|---|---:|---:|---:|---:|---:|---:|
| bollinger_reversion | `bollinger-1440m-20-2-long_only` | 15.43% | 16.26% | -22.74% | -29.87% | 3 | 25% |
| donchian_breakout | `donchian-240m-42-12-long_only` | 34.88% | 4.22% | -15.60% | -18.25% | 13 | 12% |
| ema_deadband | `ema-deadband-1440m-10-50-0.05-long_only` | 41.16% | 7.28% | -2.36% | -3.88% | 1 | 0% |
| ema_trend | `ema-1440m-10-50-long_short` | 3.37% | 11.85% | 15.61% | -15.61% | 5 | 38% |
| macd_trend | `macd-240m-24-52-18-long_short` | 30.22% | 10.65% | -20.82% | -41.95% | 52 | 25% |
| rsi_mean_reversion | `rsi-240m-25-75-long_only` | 29.22% | 38.36% | -28.17% | -31.98% | 4 | 12% |
| time_series_momentum | `momentum-1440m-20-0.05-long_only` | 10.12% | -0.40% | -9.53% | -16.50% | 12 | 12% |

## Outcome

Status: `no_stable_candidate`.

No candidate survived the predefined multi-split, drawdown, trade-count, monthly-consistency, and cost-stress gates.

No candidate passed even the base gates, so no strategy advanced to a cost-stress approval test.

The result is not a trading approval. The existing daily EMA(10,50) lead is excluded by the stability gates because it has only five confirmation trades and historical drawdown beyond the -25% limit.
