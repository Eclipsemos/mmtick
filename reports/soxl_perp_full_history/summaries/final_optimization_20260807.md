# SOXLUSDT Full-History Strategy Optimization

Generated: 2026-08-07

## Data

- Binance listing-to-date coverage: 2026-05-15 14:00:09 UTC through
  2026-08-07 13:13:15 UTC.
- Measured replay starts at 2026-05-17 16:00:00 UTC after 200 closed 15-minute
  warmup bars.
- 9,943,262 stored 250 ms Tick buckets, representing 184,237,987 Binance trade IDs
  in the measured range.
- 5 bps fee, 2 bps slippage, historical funding, 2x venue leverage, and a 62.5%
  position budget were applied.

## Method

The search used a time-ordered walk-forward process. Parameters were selected from:

- Train: 2026-05-17 through 2026-06-30.
- Validation: 2026-07-01 through 2026-07-30.
- Holdout: 2026-07-31 through the latest Tick.

The holdout was excluded from parameter tuning. The search covered direction, ATR
period and multiplier, trend-efficiency filters, reversal confirmation, ATR profit
protection, and continuation re-entry. In total, 606 segment evaluations were run.

## Selected Strategy

- Long only.
- 15-minute Tick-driven ATR trailing strategy.
- ATR period: 32.
- ATR multiplier: 3.0.
- Trend efficiency: 8 bars, minimum 0.25.
- Profit protection: disabled.
- Continuation re-entry: disabled.
- One action per bar and next-Tick fill model remain enabled.

The long-only branch was selected because it stayed profitable in train, validation,
and holdout. Higher-return long/short finalists failed the holdout.

## Walk-Forward Results

Each row starts from 100,000 USDT independently.

| Segment | Net return | Max drawdown |
|---|---:|---:|
| May 17-31 | +33.32% | -14.66% |
| June | +36.41% | -20.65% |
| July 1-30 validation | +23.45% | -26.71% |
| July 31-August 7 holdout | +17.04% | -14.34% |

## Continuous Full-History Result

| Metric | Previous strategy | Selected strategy |
|---|---:|---:|
| Final equity | 41,692.75 USDT | 274,471.47 USDT |
| Net return | -58.31% | +174.47% |
| Maximum drawdown | -75.07% | -26.71% |
| Completed round trips | 191 | 123 |
| Win rate | 59.69% | 42.28% |
| Profit factor | 0.78 | 1.48 |
| Fees | 15,043.45 USDT | 25,808.98 USDT |
| Funding | -780.31 USDT | -2,428.58 USDT |

The lower win rate is intentional: the selected strategy stops clipping winners with
the prior 0.5 ATR profit trail and avoids structurally weak short trades. Its edge comes
from a larger average payoff rather than a high percentage of winning trades.

## Finalist Holdout Check

| Finalist | Holdout return | Max drawdown |
|---|---:|---:|
| Long ATR(32) x 3, efficiency 8 / 0.25 | +17.04% | -14.34% |
| Defensive long with 4 / 1.5 ATR profit protection | +2.01% | -9.37% |
| Defensive long without profit protection | -0.98% | -9.37% |
| Defensive long/short | -13.17% | -18.16% |
| Fast long/short | -15.67% | -25.28% |

## Limitations

The market history covers less than three months because the contract is new. The
replay does not model order-book depth, exchange rejection, API latency, liquidation,
or service outages. The holdout contains only ten completed trades for the selected
strategy. Results are evidence for the implemented configuration, not a forecast.

Source artifacts:

- `optimization_stage1_atr_direction.json`
- `optimization_stage2_refined_atr.json`
- `optimization_stage3_trend_filter.json`
- `optimization_stage4_profit_exit.json`
- `optimization_stage5_reversal_confirmation.json`
- `optimization_stage6_continuation_reentry.json`
- `optimization_stage7_holdout.json`
- `optimization_stage8_full_history.json`
- `optimization_stage9_monthly_stability.json`
- `final_standard_replay/atr_tick_grid_20260807T142654Z.json`
