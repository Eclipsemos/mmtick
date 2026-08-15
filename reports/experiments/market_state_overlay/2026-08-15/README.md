# Binance Market-State Overlay

This research-only study uses the last complete prior UTC-day Binance futures metric snapshot to
set the next day's exposure on the frozen BTC/ETH four-factor anchor. It searches open interest,
taker flow, and global/top-trader crowding states using 2021-2023 discovery and 2024-2025
validation. Reused 2026 data never selects the signal, threshold, or exposure.

## Selected Candidate

The selected signal is an ETH price/open-interest interaction normalized over 180 closed 4h bars.
Absolute z-scores at or above 1.0 use a `2.0x` overlay; other states use `1.0x`. The anchor itself
has `4x` internal leverage, so peak modeled notional is approximately `8x` equity. The mapping
uses only the last complete prior UTC-day snapshot.

| Split | Return | Max DD | 15% months |
|---|---:|---:|---:|
| 2021-2023 discovery | +799.47% | -27.56% | 11/36 |
| 2024-2025 validation | +165.31% | -23.51% | 5/24 |
| 2026 reused confirmation | +82.34% | -13.30% | 2/8 |
| 2026 stress costs | +61.15% | -16.24% | 1/8 |

Stress uses 10 bps component fees, 5 bps component slippage, and 15 bps overlay turnover cost.
The exact development winner fails the revised 4/8 confirmation gate and the stress monthly
coverage gate. The non-selective top-200 diagnostic found no passing configuration, and the extra
one-day delay also failed.

Decision: `rejected_after_confirmation`. Approved for trading: `false`. The evidence is reused,
daily-close drawdown does not model intraday liquidation, and no borrowing cost is included. See
`market-state-overlay-20260815-142603-459615` for the corrected machine-readable evidence. The
earlier 141824 report is retained but invalid because it used same-day metric information.

```bash
.venv/bin/python scripts/update_futures_metrics.py --start 2021-01-01
.venv/bin/python scripts/mine_market_state_overlay.py
```
