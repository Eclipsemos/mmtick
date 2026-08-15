# Market-State Volatility Overlay

This research-only study applies a prior-UTC-day Binance futures market state and a causal daily
volatility target to the frozen four-factor BTC/ETH anchor. Numeric ranking uses 2021-2025 only,
but the search scope was revised after prior 2026 diagnostics, so 2026 is reused evidence rather
than an independent holdout.

The selected state is the ETH top-position versus global-account crowding spread normalized over
540 closed 4h bars. Values at or below -1.25 use a 2.0x state exposure; other known values use
0.8x. A trailing 20-day return RMS targets 3% daily volatility and constrains the second exposure
layer to 0.6x-1.1x. Turnover is charged once on the product of both exposure layers.

| Split | Return | Max DD | Months at least +15% |
|---|---:|---:|---:|
| 2021-2023 discovery | +2001.56% | -33.87% | 13/36 |
| 2024-2025 validation | +140.28% | -27.87% | 6/24 |
| 2026 reused confirmation | +182.94% | -28.19% | 4/8 |
| 2026 stress costs | +141.52% | -32.50% | 4/8 |

Decision: `research_candidate`. Approved for trading: `false`. The result meets the defined gate
of at least half of confirmation months at +15% under base and stress costs. It does not produce
+15% every month: May and July lose money, partial August is approximately flat, 2026 is reused,
and daily-close drawdown does not model liquidation at up to approximately 8.8x notional. See
`market-state-volatility-20260815-153825-882277` for final evidence.

```bash
.venv/bin/python scripts/mine_market_state_volatility_overlay.py
```
