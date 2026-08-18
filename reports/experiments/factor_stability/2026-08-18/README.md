# Single-Factor Regime Stability

Generated: 2026-08-18T02:21:27.286734+00:00

This study changes the research question from maximizing backtest return to proving
that an individual causal factor retains out-of-sample IC across years and market states.
It is research-only and cannot create orders.

## Protocol

- Aligned BTCUSDT and ETHUSDT 4h bars; horizons: 1, 3, 6, and 18 bars.
- Expanding yearly walk-forward polarity; forward-label embargo at every year boundary.
- Non-overlapping horizon samples for IC and Bonferroni familywise significance control.
- Regimes: causal bull/bear/sideways crossed with high/low trailing volatility.
- Cost-aware labels include 14 bps round-trip fees/slippage and realized funding.
- 2026 is reused confirmation, not a fresh holdout.

## Results

| Asset | Factors | Candidates | Development eligible | Selected | Development net IC | 2026 net IC | Decision |
|---|---:|---:|---:|---|---:|---:|---|
| btc_perp | 47 | 188 | 5 | `own_ret_6-h1` | +0.0511 | +0.0037 | `rejected_after_reused_confirmation` |
| eth_perp | 47 | 188 | 2 | `other_ret_6-h1` | +0.0442 | +0.0086 | `rejected_after_reused_confirmation` |

## btc_perp Development Ranking

| Rank | Factor | Horizon | Net IC | Positive years | Positive regimes | Adjusted p | All gates |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | `own_ret_6` | 1 | +0.0511 | 100% | 100% | 0.000326 | yes |
| 2 | `own_trend_6` | 1 | +0.0413 | 100% | 100% | 0.0206 | yes |
| 3 | `other_ret_6` | 1 | +0.0477 | 100% | 100% | 0.00148 | yes |
| 4 | `own_ret_1` | 1 | +0.0475 | 100% | 100% | 0.00165 | yes |
| 5 | `other_ret_1` | 1 | +0.0429 | 100% | 83% | 0.0111 | yes |

Selected-candidate gate audit:

- `cost_adjusted_ic`: pass
- `bonferroni_significance`: pass
- `annual_consistency`: pass
- `annual_no_sign_reversal`: pass
- `regime_coverage`: pass
- `regime_consistency`: pass
- `regime_no_sign_reversal`: pass
- `cross_horizon_support`: pass

Reused-confirmation gate audit:

- `minimum_confirmation_ic`: fail
- `minimum_ic_retention`: fail

Yearly walk-forward IC:

- 2022: net IC +0.0364, samples 2188, training polarity -1.
- 2023: net IC +0.0619, samples 2188, training polarity -1.
- 2024: net IC +0.0501, samples 2194, training polarity -1.
- 2025: net IC +0.0598, samples 2188, training polarity -1.
- 2026: net IC +0.0037, samples 1336, training polarity -1.

## eth_perp Development Ranking

| Rank | Factor | Horizon | Net IC | Positive years | Positive regimes | Adjusted p | All gates |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | `other_ret_6` | 1 | +0.0442 | 100% | 83% | 0.00648 | yes |
| 2 | `own_ret_6` | 1 | +0.0527 | 100% | 100% | 0.00015 | yes |

Selected-candidate gate audit:

- `cost_adjusted_ic`: pass
- `bonferroni_significance`: pass
- `annual_consistency`: pass
- `annual_no_sign_reversal`: pass
- `regime_coverage`: pass
- `regime_consistency`: pass
- `regime_no_sign_reversal`: pass
- `cross_horizon_support`: pass

Reused-confirmation gate audit:

- `minimum_confirmation_ic`: fail
- `minimum_ic_retention`: fail

Yearly walk-forward IC:

- 2022: net IC +0.0166, samples 2188, training polarity -1.
- 2023: net IC +0.0949, samples 2188, training polarity -1.
- 2024: net IC +0.0374, samples 2194, training polarity -1.
- 2025: net IC +0.0438, samples 2188, training polarity -1.
- 2026: net IC +0.0086, samples 1336, training polarity -1.

## Decision

Transformer combination allowed: `false`.

A deep factor combination remains blocked until a development-stable single factor retains the required IC strength in a genuinely new forward month.
