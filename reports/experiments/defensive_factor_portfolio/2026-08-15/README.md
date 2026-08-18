# Defensive Factor Portfolio

This study selects existing BTC/ETH factors only by their behavior during frozen
market-state baseline loss months in 2021-2025, then searches causal monthly risk
locks. January-July 2026 is reused confirmation; partial August is excluded.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.
Best strict coverage: `4/7`; base-and-stress 7/7 configurations: `0`.

Reproduce from the repository root:

```bash
.venv/bin/python scripts/research/mine_defensive_factor_portfolio.py \
  --report-id defensive-factor-portfolio-20260815
```
