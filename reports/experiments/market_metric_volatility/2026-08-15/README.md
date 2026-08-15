# Market-Metric Volatility Target

This research-only study applies a causal daily or monthly volatility target to the frozen ETH
crowding-factor hybrid. Each evaluation split resets the 40/60 baseline allocation; up to 120
prior calendar days are used only to warm the volatility estimator. Exposure changes cost 7 bps.

The development-selected 60-day daily target returned `+78.70%` in reused 2026 confirmation with
`-21.16%` daily-close drawdown. It reached the revised `+15%` monthly target in only `3/8` months;
stress costs reduced return to `+58.76%` and still reached only `3/8`. None of the 149
development-eligible configurations passed the base and stress confirmation gates.

Decision: `rejected_after_confirmation`. Approved for trading: `false`. See
`market-metric-volatility-20260815-140418-118059` for the final evidence.

```bash
.venv/bin/python scripts/mine_market_metric_volatility_overlay.py
```
