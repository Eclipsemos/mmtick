# BTC/ETH Bar-Factor Hybrid

This research-only study tests 168 BTC/ETH EMA, Donchian, time-series momentum, and RSI sleeves
around the frozen four-factor anchor. Signals use closed 1h, 4h, or daily bars and fill at the next
strategy-bar open. Selection uses 2021-2023 discovery and 2024-2025 validation only.

The selected BTC 60-day time-series momentum hybrid returned `+162.63%` in reused 2026
confirmation with `-19.77%` daily-close drawdown, but reached `+15%` in only `3/8` months. Stress
costs returned `+134.75%` and also reached only `3/8`. None of the 130 development-eligible
hybrids passed the base and stress confirmation gates.

Decision: `rejected_after_confirmation`. Approved for trading: `false`. See
`bar-factor-hybrid-20260815-140904-249259` for the final evidence.

```bash
.venv/bin/python scripts/research/mine_bar_factor_hybrid.py
```
