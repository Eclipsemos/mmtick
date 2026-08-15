# BTCUSDT ATR Strategy Stability Audit

Generated: 2026-08-14T12:58:25.007246+00:00

This study compares structurally different ATR strategies rather than only changing one trailing-stop period and multiplier. Signals use closed 1h, 4h, or daily bars and fill at the next bar open. Base results include 5 bps fee, 2 bps slippage, and historical funding at 1.0x exposure.

Data: 2024-01-01T00:00:00+00:00 through 2026-08-11T23:59:59.999000+00:00; 91,584 complete source bars.

## ATR Families

- `atr_trailing_stop`: close-based Wilder ATR adaptive trend stop.
- `keltner_breakout`: EMA plus ATR channel breakout; exit through EMA.
- `atr_mean_reversion`: fade close distance from rolling mean measured in ATR.
- `chandelier_breakout`: prior-channel entry with Chandelier ATR trailing exit.

## Family Winners

Each winner is selected on 2024 training and 2025 validation only. The 2026 segment is then shown as confirmation.

| Family | Candidate | Train | Validation | Confirmation | Confirm DD | Trades | Positive months | Neighbor pass | Stable |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| atr_mean_reversion | `atr-mean-1440m-20-14-1.5-long_only` | 22.83% | 31.96% | -29.94% | -38.43% | 3 | 25% | 0% | no |
| atr_trailing_stop | `atr-stop-1440m-14-2-long_only` | 30.87% | -0.74% | -17.70% | -18.85% | 12 | 38% | 0% | no |
| chandelier_breakout | `chandelier-240m-55-28-4-long_only` | 36.65% | 18.74% | -13.47% | -19.05% | 9 | 25% | 20% | no |
| keltner_breakout | `keltner-1440m-20-14-2-long_short` | 37.59% | -1.81% | 15.02% | -9.04% | 3 | 25% | 20% | no |

## Gate Detail

| Family | Failed gates | Stress confirmation | Stress DD |
|---|---|---:|---:|
| atr_mean_reversion | all_splits_positive, drawdown_controlled, confirmation_trades, confirmation_months, parameter_neighborhood, cost_stress | -30.28% | -38.54% |
| atr_trailing_stop | all_splits_positive, drawdown_controlled, confirmation_months, parameter_neighborhood, cost_stress | -19.28% | -19.70% |
| chandelier_breakout | all_splits_positive, drawdown_controlled, confirmation_months, parameter_neighborhood, cost_stress | -14.71% | -20.09% |
| keltner_breakout | all_splits_positive, drawdown_controlled, confirmation_trades, confirmation_months, parameter_neighborhood, cost_stress | 14.53% | -9.27% |

## Prior Tick Baseline

The earlier `15m Tick ATR reversal, periods 14/21/28 x multipliers 2/2.5/3` study selected `ATR(14) x 3.0` at 0.64% development return, then lost -5.76% in July and -1.23% during August 1-10. It remains rejected.

## Decision

Status: `no_stable_candidate`.

No development-selected ATR family winner passed every stability gate.

An ex-post scan found 0 of 216 candidates that met the base gates. This count is diagnostic and cannot override the development-selected family-winner protocol.

ATR hypotheses were created after the archive had already been inspected. The 2026 segment is diagnostic confirmation, not pristine unseen evidence.

No result in this report is approved for paper or live trading.
