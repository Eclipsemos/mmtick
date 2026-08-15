# soxl_perp Causal Factor Mining

Generated: 2026-08-15T04:55:18.615617+00:00

This is a research-only implementation inspired by AlphaGPT's formula discovery concept. It uses a constrained causal formula DSL, not AlphaGPT's model, data pipeline, or execution stack.

## Data And Execution

- Coverage: 2026-05-15T14:00:00+00:00 to 2026-08-13T23:59:59.999000+00:00.
- Complete source bars: 8,680; funding events: 269.
- Candidate count: 324; development eligible: 17.
- Costs: 5 bps fee plus 2 bps slippage per fill; historical funding included.
- Signals: closed bar; execution: next bar open; exposure: 1.0x; liquidation not modeled.

## Causal Formula Language

- Features: `ret_1_z`, `ret_4_z`, `ret_16_z`, `trend_20_z`, `range_z`, `volume_z`, `close_location_z`, `atr_ratio_z`, `funding_z`.
- Unary operators: `NEG`, `ABS`, `SIGN`, `DELAY1`, `DECAY3`.
- Binary operators: `ADD`, `SUB`, `MUL`, `DIV`.
- Causality: Every feature uses only data through its closed bar; rolling z-scores use a trailing 32-bar window and never a full-sample normalization.

## Selected Development Formula

- ID: `60m-long_only-ret_16_z-range_z-sub-threshold-0p5`
- Formula: `(ret_16_z sub range_z)`
- Postfix tokens: `ret_16_z, range_z, SUB`
- Bar interval / direction / threshold: 60m / long_only / 0.5.

| Split | Return | Max drawdown | Trades | Positive months |
|---|---:|---:|---:|---:|
| train | 69.50% | -18.37% | 84 | 100% |
| validation | 19.07% | -25.07% | 63 | 100% |
| confirmation | 24.02% | -5.86% | 17 | 100% |

Top-ten development-neighbor confirmation pass rate: 90%.

| Stability gate | Pass |
|---|---|
| all_splits_positive | yes |
| drawdown_controlled | no |
| confirmation_trades | yes |
| confirmation_months | yes |
| parameter_neighborhood | yes |
| cost_stress | no |

Stress confirmation at 10 bps fee plus 5 bps slippage per fill: 20.70%, max drawdown -6.16%.

## Decision

Status: `insufficient_history`.

SOXLUSDT has insufficient independent history for factor-mining approval.

No formula in this report is authorized for paper or live execution.
