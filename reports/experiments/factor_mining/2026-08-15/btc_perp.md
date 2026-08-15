# btc_perp Causal Factor Mining

Generated: 2026-08-15T04:55:18.615617+00:00

This is a research-only implementation inspired by AlphaGPT's formula discovery concept. It uses a constrained causal formula DSL, not AlphaGPT's model, data pipeline, or execution stack.

## Data And Execution

- Coverage: 2024-01-01T00:00:00+00:00 to 2026-08-11T23:59:59.999000+00:00.
- Complete source bars: 91,584; funding events: 2,862.
- Candidate count: 648; development eligible: 4.
- Costs: 5 bps fee plus 2 bps slippage per fill; historical funding included.
- Signals: closed bar; execution: next bar open; exposure: 1.0x; liquidation not modeled.

## Causal Formula Language

- Features: `ret_1_z`, `ret_4_z`, `ret_16_z`, `trend_20_z`, `range_z`, `volume_z`, `close_location_z`, `atr_ratio_z`, `funding_z`.
- Unary operators: `NEG`, `ABS`, `SIGN`, `DELAY1`, `DECAY3`.
- Binary operators: `ADD`, `SUB`, `MUL`, `DIV`.
- Causality: Every feature uses only data through its closed bar; rolling z-scores use a trailing 32-bar window and never a full-sample normalization.

## Selected Development Formula

- ID: `240m-long_only-atr_ratio_z-delay1-threshold-0p5`
- Formula: `delay1(atr_ratio_z)`
- Postfix tokens: `atr_ratio_z, DELAY1`
- Bar interval / direction / threshold: 240m / long_only / 0.5.

| Split | Return | Max drawdown | Trades | Positive months |
|---|---:|---:|---:|---:|
| train | 29.75% | -25.98% | 72 | 64% |
| validation | 5.12% | -30.23% | 73 | 58% |
| confirmation | -24.06% | -30.51% | 48 | 38% |

Top-ten development-neighbor confirmation pass rate: 0%.

| Stability gate | Pass |
|---|---|
| all_splits_positive | no |
| drawdown_controlled | no |
| confirmation_trades | yes |
| confirmation_months | no |
| parameter_neighborhood | no |
| cost_stress | no |

Stress confirmation at 10 bps fee plus 5 bps slippage per fill: -29.69%, max drawdown -31.73%.

## Decision

Status: `rejected_after_confirmation`.

No development-selected formula passed all confirmation stability gates.

No formula in this report is authorized for paper or live execution.
