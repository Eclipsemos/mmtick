# Support/Resistance Strategy Selection

## Scope

This is an offline research extension of the BTC/ETH H4 scan. It tested 128 new
configurations across six families: asset-specific momentum confirmation, D1/H4
level confluence, and causal ATR-volatility filters for fade and breakout entries.
Signals use a closed H4 bar, fills use the next H4 open, and realized funding is
included. Normal costs are 14 bps round trip; stress costs are 30 bps. D1 states are
lagged to the last completed UTC day, so the MTF tests do not use the current day's
close.

## Findings

The strongest new single-leg result is
`breakout_mtf_eth_perp_d2.5_h18`: validation (2024–2025) return `+147.87%`,
stress `+118.58%`, 79 trades, PF `1.46`, and max drawdown `-26.32%`. It remains
positive in 2026 confirmation at `+16.54%` normal and `+11.80%` stress. The simpler
`breakout_highvol_eth_perp_m0.75_h18` is more useful for phase coverage: it is
positive in 2020–2021 (`+108.59%`), 2022–2023 (`+83.45%`), 2024–2025 (`+122.82%`),
and 2026 (`+18.49%`), with 2026 stress `+14.40%`.

BTC multi-timeframe fade configurations do not survive confirmation. For example,
`fade_mtf_btc_perp_d2.5_h18` made `+113.49%` in validation but `-31.57%` in 2026
(`-37.37%` stress), so it is rejected as a likely regime-specific effect.

The best tested portfolio is the existing `fade_btc_perp_l2_e2.5_h12` plus
`breakout_eth_perp_b0_h18` at 50/50: validation `+74.20%` normal / `+46.12%`
stress, confirmation `+17.28%` / `+11.18%` stress, and validation drawdown
`-14.30%`.

## Decision

No strategy is approved for paper or live execution. Keep the ETH breakout family
and the 50/50 portfolio in forward observation only. Require fresh post-2026 data,
walk-forward parameter selection, trade-level cost checks, and a placebo/distance
control before freezing a candidate. Full metrics are in the accompanying JSON.
