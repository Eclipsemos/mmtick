# Monthly Robust Ensemble

This study selects fixed-weight multi-sleeve portfolios by monthly stability
across 2024/2025 under base and stress costs. January-July 2026 is reused
confirmation and partial August is excluded from strict counts.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.
Best strict coverage: `5/7`; base-and-stress 7/7 configurations: `0`.

Reproduce from the repository root:

```bash
.venv/bin/python scripts/mine_monthly_robust_ensemble.py \
  --report-id monthly-robust-ensemble-20260815
```
