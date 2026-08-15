# ETH Retail-Spread Volatility Overlay v1

Status: `provisional_forward_candidate`. This definition is research-only and is not approved for
paper or live trading.

## Frozen Definition

| Item | Value |
|---|---:|
| Baseline | Frozen BTC/ETH four-factor event anchor |
| Baseline internal leverage | 4.0x |
| State input | ETH top-position/global-account log-ratio z-score |
| State normalization | 540 closed 4h bars |
| State timing | Last complete prior UTC-day 4h snapshot |
| High state | z-score at or below -1.25 |
| State exposure | 2.0x high / 0.8x otherwise / 1.0x while unavailable |
| Volatility estimator | Trailing 20 closed daily returns, RMS |
| Volatility target | 3.0% daily; 0.6x-1.1x exposure |
| Rebalance | Daily |
| Effective outer exposure | Approximately 0.48x-2.20x |
| Maximum modeled notional | Approximately 8.8x equity |
| Base costs | 5 bps fee + 2 bps slippage; 7 bps combined-overlay turnover |
| Stress costs | 10 bps fee + 5 bps slippage; 15 bps combined-overlay turnover |
| Evidence lock | 2026-08-14 UTC |
| Forward evidence starts | 2026-08-15 UTC |

The machine-readable definition is in
[eth_retail_spread_volatility_overlay_v1.json](eth_retail_spread_volatility_overlay_v1.json).
Final evidence is in
[market-state-volatility-20260815-153825-882277.md](../../reports/experiments/market_state_volatility/2026-08-15/market-state-volatility-20260815-153825-882277.md).

## Evidence

| Split | Return | Max DD | Months at least +15% |
|---|---:|---:|---:|
| 2021-2023 discovery | +2001.56% | -33.87% | 13/36 |
| 2024-2025 validation | +140.28% | -27.87% | 6/24 |
| 2026 reused confirmation | +182.94% | -28.19% | 4/8 |
| 2026 stress costs | +141.52% | -32.50% | 4/8 |

The target months in 2026 are January, February, March, and June. April is positive but below the
target; May and July lose money; partial August is approximately flat. This candidate therefore
meets the research gate of at least half the months above +15%, not a guarantee of +15% or even a
profit in every month. Only 3 of the top 20 development configurations passed the reused
confirmation gate.

The 2026 period has already been examined repeatedly and is not a fresh holdout. Promotion
requires fresh forward evidence and an intraday shared-margin liquidation replay. No execution
integration is authorized.
