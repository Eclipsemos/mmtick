# Fast Trend Complement

This study tests predeclared 4h/daily BTC/ETH time-series momentum sleeves around
the frozen market-state strategy. Selection uses 2021-2025 only; January-July 2026
is reused confirmation and partial August is excluded.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.
Best strict coverage: `4/7`; base-and-stress 7/7 configurations: `0`.

Reproduce from the repository root:

```bash
.venv/bin/python scripts/research/mine_fast_trend_complement.py \
  --report-id fast-trend-complement-20260815
```
