# SOXLUSDT Volatility-Spread Exploration

Generated: 2026-08-14T03:17:45.313296+00:00

## Method

The volatility spread is the ratio of short-window to long-window normalized true range. Entries require a prior-channel breakout. `compression_release` additionally requires a low-volatility observation during the preceding 16 bars. Signals use closed bars and execute at the next bar open.

Costs: 5 bps fee and 2 bps slippage per fill, Binance funding included. The first 200 bars are indicator warmup. August is a frozen holdout and did not participate in selection.

## Selected Research Candidate

```json
{
  "variant": "compression_release",
  "direction": "long_only",
  "fast_window": 24,
  "slow_window": 64,
  "entry_ratio": 1.0,
  "exit_ratio": 0.8,
  "breakout_window": 24,
  "stop_atr": 3.5,
  "max_hold_bars": 96,
  "exposure": 1.25,
  "compression_ratio": 0.85,
  "compression_lookback": 16
}
```

Decision: **provisional_tick_replay_candidate**. positive frozen holdout, but sample size and profit concentration are insufficient.

Positive holdouts among the top train/validation finalists: 17/20.

| Period | Return | Geometric/day | Days >= 5% | Win rate | Trades | Max DD | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| train | +32.66% | +0.63% | +4.44% | +50.00% | 10 | -6.00% | 4.95 |
| validation | +13.05% | +0.40% | +3.23% | +80.00% | 5 | -5.73% | 22.84 |
| holdout | +3.12% | +0.31% | +10.00% | +50.00% | 4 | -14.91% | 1.28 |
| full | +54.64% | +0.51% | +4.65% | +57.89% | 19 | -14.91% | 3.12 |

## Evidence Quality

The full replay completed only 19 trades. The five largest winning trades account for +76.46% of gross profit; the frozen holdout contains only 4 trades. These are insufficient observations for production promotion.

| Gate | Passed |
|---|:---:|
| At least 20 holdout trades | no |
| Top-five profit concentration <= 50% | no |
| Positive frozen holdout | yes |
| At least 70% of one-at-a-time neighbors positive in holdout | yes |
| Geometric daily return >= 5% | no |

## Parameter Neighborhood

One parameter was changed at a time across 39 nearby configurations. 35 (+89.74%) were positive in the frozen holdout; 33 were positive in both July and the frozen holdout.

Median full return was +49.90%; median holdout return was +3.12%. Some fast- and slow-window neighbors still lost money, so this is local support rather than proof of robust parameters. Full rows are in `results.json`.

## Walk-Forward Checks

| Development | Forward test | Direction | Test return | Trades | Max DD |
|---|---|---|---:|---:|---:|
| 2026-05-17 through 2026-06-30 | 2026-07-01 through 2026-07-31 | long_short | +11.26% | 20 | -12.11% |
| 2026-05-17 through 2026-07-31 | 2026-08-01 through 2026-08-10 | long_only | +3.12% | 4 | -14.91% |

## Next-Persisted-Tick Fill Check

Signals and risk checks remain on closed official 15m bars; each pending action fills on the first persisted 250ms aggregate Tick inside the next bar.

| Period | Bar-open return | Tick-fill return | Difference | Median fill delta | Max fill delta | Max delay |
|---|---:|---:|---:|---:|---:|---:|
| holdout | +3.12% | +3.19% | +0.07% | 0.761 bps | 3.657 bps | 1228 ms |
| full | +54.64% | +55.53% | +0.89% | 1.069 bps | 23.538 bps | 1228 ms |

This validates fill timing and price only. Drawdown remains marked on 15m closes, not on every intrabar Tick.

## ATR Baseline Diversification

Across 86 UTC days, Pearson daily-return correlation with the ATR(32) x 3 long-only Tick replay was 0.473. Both paths lost on 5 days.

| Daily-rebalanced path | Return | Geometric/day | Daily-close max DD |
|---|---:|---:|---:|
| ATR baseline | +173.02% | +1.17% | -17.83% |
| Volatility spread | +54.64% | +0.51% | -9.20% |
| 75% ATR / 25% spread | +144.44% | +1.04% | -13.69% |
| 50% ATR / 50% spread | +114.40% | +0.89% | -9.44% |
| 25% ATR / 75% spread | +84.09% | +0.71% | -6.76% |

Daily-path comparison only: ATR uses Tick replay while volatility spread uses official 15m bars and next-open fills. Intraday drawdowns are not comparable.

## Exposure Ladder

| Exposure | Full return | Full max DD | Holdout return | Holdout max DD |
|---:|---:|---:|---:|---:|
| 0.50x | +19.95% | -6.43% | +1.50% | -6.43% |
| 1.00x | +42.43% | -12.22% | +2.66% | -12.22% |
| 1.25x | +54.64% | -14.91% | +3.12% | -14.91% |
| 1.50x | +67.52% | -17.49% | +3.49% | -17.49% |
| 2.00x | +95.31% | -22.33% | +4.00% | -22.33% |

## High-Exposure Stress Test

| Target exposure | Tick-fill return | Geometric/day | 15m-close max DD |
|---:|---:|---:|---:|
| 2.0x | +97.12% | +0.79% | -22.22% |
| 3.0x | +162.78% | +1.13% | -30.85% |
| 5.0x | +328.98% | +1.71% | -45.18% |
| 10.0x | +863.20% | +2.67% | -71.07% |
| 15.0x | +1079.90% | +2.91% | -88.34% |
| 20.0x | +112.16% | +0.88% | -99.13% |

This is an intentionally optimistic stress test: liquidation is not modeled and risk is marked only at 15m closes. It must not be interpreted as executable leverage guidance. Even the best tested geometric daily result remains below 5%.

## 5% Daily Target Check

Over 86 active UTC days, 5% daily compounding requires `+6541.71%` cumulative return. The selected candidate produced +54.64%, with +4.65% of active days at or above +5%.

The 5% daily objective was not achieved. This remains an exploratory bar-level candidate, not a production strategy. Next-persisted-Tick fills have been checked, but substantially more out-of-sample trades and full intrabar risk measurement are still required.
