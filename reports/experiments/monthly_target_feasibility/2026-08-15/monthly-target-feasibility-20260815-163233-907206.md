# Strict +15% Monthly Target Feasibility Audit

Generated: `2026-08-15T16:34:12.038760+00:00`

## Decision

**Rejected. The strict goal is not achieved by a valid research protocol and this is not approved for trading.**

A formula can be made to clear all seven complete 2026 months after inspecting those same months. That result is an ex-post feasibility boundary, not out-of-sample evidence. Once the composition is constrained by causal volatility sizing that survives development risk controls, only 3/7 complete months reach +15% under both cost models.

## Monthly Results

| Month | Boundary base | Boundary stress | Risk-controlled base | Risk-controlled stress | Goal audit |
|---|---:|---:|---:|---:|---|
| 2026-01 | +24.03% | +22.41% | +14.46% | +13.70% | complete |
| 2026-02 | +23.91% | +22.90% | +19.41% | +19.23% | complete |
| 2026-03 | +40.49% | +38.82% | +15.91% | +15.72% | complete |
| 2026-04 | +32.67% | +29.99% | +10.62% | +10.05% | complete |
| 2026-05 | +24.03% | +21.34% | +8.07% | +7.72% | complete |
| 2026-06 | +32.16% | +30.80% | +26.24% | +25.87% | complete |
| 2026-07 | +39.35% | +38.52% | +15.00% | +14.97% | complete |
| 2026-08 | -3.23% | -4.80% | -0.31% | -0.62% | **partial; excluded** |

| Replay | Complete target months | Total incl. partial | Daily-close max DD |
|---|---:|---:|---:|
| Ex-post boundary, base | 7/7 | +533.20% | -20.93% |
| Ex-post boundary, stress | 7/7 | +468.16% | -22.19% |
| Risk-controlled, base | 3/7 | +174.09% | -13.48% |
| Risk-controlled, stress | 3/7 | +167.49% | -13.79% |

## Ex-Post Boundary

- 10% frozen four-factor anchor, 80% ETH daily MACD(10,30,9) long/short, 5% ETH 60m RSI(14,30,70) long-only, and 5% BTC 15-day shock reversal long-only.
- 6x outer leverage, 25% monthly loss lock, and 15% monthly profit lock.
- Base costs are 5 bps fee + 2 bps slippage per fill and 7 bps overlay turnover. Stress costs are 10 + 5 bps and 15 bps overlay turnover.

The sleeve choices and weights were found after inspecting 2026. The apparent 7/7 success therefore cannot be used as evidence that the same rule will work prospectively.

## Reverse Audit

The audit scanned `969` strictly positive 5%-step weight combinations and 2x through 8x leverage: `6,783` configurations total.

- Profitable in both 2021-2023 and 2024-2025: `2,313`.
- Profitable with daily-close drawdown no worse than -35% in both splits: `0`.

The ex-post 7/7 weights returned `+49.62%` with `-83.18%` drawdown in 2021-2023, then `-33.95%` with `-76.10%` drawdown in 2024-2025.

## Causal Risk Control

The same fixed composition was constrained with a trailing 40-day daily RMS, a 2% daily volatility target, 1x-3x exposure, a 10% monthly loss lock, and the same 15% profit lock. It passes the positive-return and -35% drawdown gates in both development splits. In reused confirmation it keeps modeled daily-close drawdown near 14%, but removes the claimed monthly consistency: only 3/7 complete months clear +15% in either cost model.

## Limitations

- Confirmation year 2026 was reused throughout prior research and is not a fresh holdout.
- Daily-close drawdown can miss intraday liquidation risk; liquidation is not modeled.
- August is incomplete and is shown only as a partial diagnostic.
- Achieving a target in every calendar month is not treated as a relaxed coverage goal.
