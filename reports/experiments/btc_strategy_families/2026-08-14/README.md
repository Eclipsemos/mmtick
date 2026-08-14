# BTCUSDT Non-ATR Strategy Families

Generated: 2026-08-14T09:20:52.695821+00:00

All signals use closed 1h, 4h, or 1d bars and fill at the next bar open. Results include 5 bps fees and 2 bps slippage per fill plus historical funding. Confirmation data was not used for candidate selection.

## Families

- EMA trend: fast/slow exponential moving-average direction.
- EMA deadband: the same trend signal, but cash when EMA separation is too small.
- Donchian breakout: enter on a prior-channel break and exit through a shorter channel.
- Time-series momentum: direction from a fixed historical return and neutral threshold.
- RSI mean reversion: fade RSI extremes and exit at RSI 50.

Sequential note: the first 72 candidates revealed confirmation results before EMA deadband variants were added. Deadband results are diagnostic and are not fresh confirmation evidence.

## Family Winners

| Family | Candidate | Train | Validation | Confirmation | Confirm DD | Trades |
|---|---|---:|---:|---:|---:|---:|
| donchian_breakout | `donchian-240m-42-12-long_only` | 34.88% | 4.22% | -15.60% | -18.25% | 13 |
| ema_deadband | `ema-deadband-1440m-10-50-0.05-long_only` | 41.16% | 7.28% | -2.36% | -3.88% | 1 |
| ema_trend | `ema-1440m-10-50-long_short` | 3.37% | 11.85% | 15.61% | -15.61% | 5 |
| rsi_mean_reversion | `rsi-240m-25-75-long_only` | 29.22% | 38.36% | -28.17% | -31.98% | 4 |
| time_series_momentum | `momentum-1440m-20-0.05-long_only` | 10.12% | -0.40% | -9.53% | -16.50% | 12 |

## Selected Development Winner

Candidate: `rsi-240m-25-75-long_only`

Train 29.22%; validation 38.36%; confirmation -28.17%.

### Confirmation Monthly Returns

| Month | Return |
|---|---:|
| 2026-01 | -8.10% |
| 2026-02 | -9.87% |
| 2026-03 | 0.00% |
| 2026-04 | 0.00% |
| 2026-05 | 0.45% |
| 2026-06 | -13.67% |
| 2026-07 | 0.00% |
| 2026-08 | 0.00% |

### Exposure Stress

| Exposure | Confirmation return | Max DD | Bankrupt |
|---:|---:|---:|---|
| 0.5x | -14.70% | -16.82% | no |
| 1.0x | -28.17% | -31.98% | no |
| 2.0x | -51.48% | -57.46% | no |
| 3.0x | -70.07% | -77.05% | no |
| 4.0x | -100.86% | -100.86% | yes |

## Notable But Unapproved Leads

`ema-1440m-10-50-long_short` was positive in all three splits, but its training max drawdown was -50.23% and confirmation contained only 5 completed trades. Confirmation geometric monthly return was 1.83%.

| Exposure | Confirmation return | Monthly geometric | Max DD | Bankrupt |
|---:|---:|---:|---:|---|
| 0.5x | 8.30% | 1.00% | -7.97% | no |
| 1.0x | 15.61% | 1.83% | -15.61% | no |
| 2.0x | 26.71% | 3.00% | -29.92% | no |
| 3.0x | 32.50% | 3.58% | -42.91% | no |
| 4.0x | 32.53% | 3.58% | -54.57% | no |

## Decision

Status: `rejected_after_confirmation`.

The 1x buy-and-hold confirmation benchmark returned -28.19% with -40.66% max drawdown.

The 25% monthly target is an evaluation threshold, not a parameter-selection override. No result is production-approved by this exploratory study.
