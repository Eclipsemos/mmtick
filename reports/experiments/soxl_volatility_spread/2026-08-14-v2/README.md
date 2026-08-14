# SOXLUSDT Volatility-Spread Phase 2

Generated: 2026-08-14T04:02:29.060423+00:00

## Design

Three spread definitions were compared: normalized true range, close-return volatility, and candle-body range. Optional fast/slow volume confirmation was included. Signals use closed 15m bars; selected-candidate verification fills on the next persisted 250ms aggregate Tick.

Candidate ranking used May 17 through July 31. August 1-10 was confirmation only. August 11-13 was not accessed until every per-measure candidate and the global measure (`true_range`) had been locked.

## Locked Candidates And Fresh Holdout

| Candidate | Development | Confirmation | Fresh reset | Fresh continuous | Fresh geo/day | Fresh trades | Local stable | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| `true_range` | +180.23% | +5.93% | +0.74% | +0.74% | +0.25% | 3 | +62.50% | yes |
| `return_volatility` | +4.56% | -1.88% | +0.00% | +0.00% | +0.00% | 0 | +0.00% | no |
| `body_range` | +14.40% | +4.31% | -2.05% | -2.05% | -0.69% | 1 | +53.33% | yes |
| `phase_one_fixed` | +55.53% | -- | -1.95% | -1.95% | -0.65% | 2 | -- | -- |

## Locked Global Candidate

```json
{
  "variant": "compression_release",
  "direction": "long_short",
  "fast_window": 12,
  "slow_window": 64,
  "entry_ratio": 1.1,
  "exit_ratio": 0.8,
  "breakout_window": 24,
  "stop_atr": 2.5,
  "max_hold_bars": 96,
  "exposure": 1.25,
  "compression_ratio": 0.85,
  "compression_lookback": 16,
  "spread_measure": "true_range",
  "minimum_volume_ratio": null
}
```

Decision: **insufficient_fresh_evidence**. fresh holdout trade count or stability gate did not establish robust evidence.

## Development Structure

| Month | Return | Geometric/day | Daily-close max DD |
|---|---:|---:|---:|
| 2026-05 | +14.92% | +0.93% | -4.70% |
| 2026-06 | +64.38% | +1.67% | -4.93% |
| 2026-07 | +39.69% | +1.08% | -6.98% |
| 2026-08 | +6.19% | +0.60% | -6.77% |

Development trades: 26 long / 38 short. Net PnL: 42,521.57 long / 137,703.96 short.
Shorts contributed +76.41% of positive directional net PnL. The five largest wins contributed +54.79% of gross profit.
Intrabar reconstruction scanned 1,278,237 persisted Ticks across all positions. Maximum Tick-path drawdown was -13.59% at 2026-07-09T14:21:29.742000+00:00, versus -11.11% when marked only at 15m closes.

## Fresh Holdout Daily Returns

| UTC date | Return |
|---|---:|
| 2026-08-11 | -1.66% |
| 2026-08-12 | +3.77% |
| 2026-08-13 | -1.28% |

## ATR Baseline Comparison Through August 10

Daily-return correlation with ATR(32) x 3 was 0.192.

| Path | Return | Geometric/day | Daily-close max DD |
|---|---:|---:|---:|
| ATR baseline | +174.94% | +1.18% | -17.83% |
| Locked spread | +180.23% | +1.21% | -6.98% |
| 50/50 daily-rebalanced mix | +196.11% | +1.27% | -9.81% |

### Fresh Holdout Portfolio

The three-day ATR/spread correlation rose to 0.969; both paths lost together on 2 days.

| Fresh path | Three-day return | Geometric/day |
|---|---:|---:|
| ATR baseline | -1.55% | -0.52% |
| Locked spread | +0.74% | +0.25% |
| 50/50 daily-rebalanced mix | -0.41% | -0.14% |

ATR uses Tick-level signal replay. The spread uses closed 15m-bar signals and the first persisted Tick for fills; spread risk is marked at 15m closes. Intraday drawdowns are therefore not comparable.

## 50/50 Portfolio Scale Stress

| Capital scale | Approx. exposure | Development geo/day | Daily-close max DD | Fresh return |
|---:|---:|---:|---:|---:|
| 1.0x | 1.25x | +1.27% | -9.81% | -0.41% |
| 2.0x | 2.50x | +2.34% | -19.21% | -1.04% |
| 3.0x | 3.75x | +3.24% | -28.19% | -1.88% |
| 4.0x | 5.00x | +3.98% | -36.76% | -2.92% |
| 5.0x | 6.25x | +4.58% | -44.91% | -4.16% |
| 6.0x | 7.50x | +5.04% | -52.64% | -5.58% |

Linear scaling of the 50/50 daily-rebalanced net-return series; diagnostic only, with no liquidation or cross-strategy intraday margin model.
Minimum tested development scale reaching 5% geometric daily return: 6.00x.

## High-Exposure Target Test

| Target exposure | Development return | Development geo/day | Tick-path max DD | Fresh 3-day return |
|---:|---:|---:|---:|---:|
| 1.25x | +180.23% | +1.21% | -13.59% | +0.74% |
| 2.00x | +372.72% | +1.82% | -20.95% | +1.10% |
| 3.00x | +766.31% | +2.54% | -30.13% | +1.48% |
| 5.00x | +2142.13% | +3.68% | -48.30% | +1.93% |
| 7.50x | +4828.06% | +4.64% | -69.58% | +1.92% |
| 10.00x | +7280.75% | +5.13% | -89.45% | +1.30% |

Liquidation is not modeled. The 10x path reaches 5% per day only in the development sample while suffering an 89% Tick-path drawdown and failing to repeat that return in the fresh holdout. It is not an executable solution.

## 5% Daily Target

The locked global candidate produced +0.25% geometric daily return in the fresh holdout. The 5% target was not met.

The fresh holdout contains only three UTC days. A positive result is useful directional evidence, not sufficient evidence for production or leverage.

### Return-Path Feasibility

A deterministic circular block bootstrap tested 360,000 resampled 30/90-day paths without
changing parameters or resampling the August 11-13 holdout. At 10x exposure, only 51.71%-54.06%
of 90-day paths reached 5% geometric daily return, while 93.27%-95.74% suffered at least a 50%
daily-close drawdown. The observed development Tick-path drawdown was -89.45% before modeling
liquidation.

No tested exposure both reached the 5% development target and stayed inside a diagnostic 30%
Tick-path drawdown guard. Fixed leverage is rejected as the route to the target. The next structural
experiment should increase independent opportunity coverage with a risk-normalized multi-horizon
volatility-spread ensemble, subject to a combined exposure cap.

Detailed output: [target_feasibility.md](target_feasibility.md).
