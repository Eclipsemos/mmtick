# Monthly Target Regime Router Audit

This directory records the reproducible test of persistent MACD as either a fixed
sleeve or a causal router around the frozen market-state strategy.

Decision: `rejected_no_strict_monthly_solution`. No strategy is approved for trading.

| Family | Eligible configs | Best strict months | 7/7 configs |
|---|---:|---:|---:|
| Fixed mixture | 736 | 4/7 | 0 |
| Causal regime route | 828 | 4/7 | 0 |

Features use full bar-history warmup and development runs continuously before split
slicing. Selection uses 2021-2025 only; the confirmation account resets at
2026-01-01.
January-July 2026 is reused confirmation, and partial August is excluded. The result
rejects this direction as a solution to the strict every-month +15% requirement.

Reproduce from the repository root:

```bash
.venv/bin/python scripts/research/mine_monthly_target_regime_router.py \
  --report-id monthly-target-regime-router-20260815
```
