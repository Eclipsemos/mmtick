# Volatility Order-Flow Router

This study uses BTC realized volatility known at the prior UTC close to route
between the frozen market-state strategy and development-selected order flow.
Selection uses 2024/2025 only; January-July 2026 is reused confirmation.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.
Best strict coverage: `5/7`; base-and-stress 7/7 configurations: `0`.

Reproduce from the repository root:

```bash
.venv/bin/python scripts/research/mine_volatility_order_flow_router.py \
  --report-id volatility-order-flow-router-20260815
```
