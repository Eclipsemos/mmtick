# Order-Flow Complement

This study combines development-eligible BTC 4h order-flow factors with the frozen
market-state strategy and causal monthly risk locks. Development is limited to
2024/2025; January-July 2026 is reused confirmation and August is excluded.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.
Best strict coverage: `5/7`; base-and-stress 7/7 configurations: `0`.

Reproduce from the repository root:

```bash
.venv/bin/python scripts/mine_order_flow_complement.py \
  --report-id order-flow-complement-20260815
```
