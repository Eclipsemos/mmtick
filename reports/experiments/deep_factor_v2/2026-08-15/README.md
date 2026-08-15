# Deep factor v2 study

This directory contains the authoritative 2026 confirmation run for the cross-asset,
multi-horizon Transformer factor. It is research-only and has no execution integration.

## Protocol

- Aggregate aligned BTCUSDT and ETHUSDT 15m history into causal 4h bars.
- Fit three Transformer seeds on 2020-2022 and choose checkpoints on 2023.
- Select signal and risk controls on 2024-2025 only.
- Reuse 2026-01-01 through 2026-08-10 as confirmation, with next-bar fills, historical funding,
  5+2 bps base costs, and 10+5 bps stress costs.
- Require positive train and validation returns, at least 12 trades in each, at least 50% positive
  months, and maximum drawdown no worse than 35% before portfolio selection.

## Result

The run is rejected. BTC produced 249 base development candidates and ETH produced 169, but
neither asset produced a risk-eligible configuration. The portfolio layer therefore selected no
weights and did not promote fallback diagnostics into a strategy.

The fallback BTC diagnostic lost 65.53% in confirmation with 79.75% maximum drawdown; the ETH
diagnostic lost 87.83% with 96.90% maximum drawdown. Under 10+5 bps costs they lost 70.47% and
89.26%, respectively. These values are negative evidence, not candidate performance.

Authoritative artifacts:

- [`deep-factor-v2-20260815-103121-416473.md`](deep-factor-v2-20260815-103121-416473.md)
- [`deep-factor-v2-20260815-103121-416473.json`](deep-factor-v2-20260815-103121-416473.json)
