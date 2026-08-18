# Volatility Guarded Trend

This study guards the frozen baseline/trend mix with prior-day BTC realized volatility.
The coarse grid is development-only; the local neighborhood is explicitly marked
post-confirmation and is not approved for trading.

Decision: `reused_confirmation_candidate_post_confirmation_refinement`. Trading approval: `false`.
Best strict coverage: `7/7`; base-and-stress 7/7 configurations: `5`.

Reproduce from the repository root:

```bash
.venv/bin/python scripts/research/mine_volatility_guarded_trend.py \
  --report-id volatility-guarded-trend-20260815
```
