# Drawdown Recovery Trend

This study activates development-selected single or paired MACD sleeves one UTC
day after the frozen baseline breaches a monthly drawdown threshold. Selection uses
2024/2025 only; January-July 2026 is reused confirmation.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.
Best strict coverage: `5/7`; base-and-stress 7/7 configurations: `0`.

Reproduce from the repository root:

```bash
.venv/bin/python scripts/mine_drawdown_recovery_trend.py \
  --report-id drawdown-recovery-trend-20260815
```
