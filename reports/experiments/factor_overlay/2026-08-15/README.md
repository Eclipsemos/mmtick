# Causal Factor-State Exposure Overlay

This research-only experiment applies next-day exposure rules to the frozen four-sleeve static
factor anchor. Rules observe only prior closed daily returns from the anchor, BTC, ETH, or the
BTC/ETH relative series. Daily and monthly rebalance frequencies, momentum/contrarian direction,
lookback, threshold, and low/high exposure are selected on 2021-2025 without reading 2026.

The search evaluated 8,160 configurations and found 127 that passed separate discovery, 2024,
and 2025 risk gates. It selected ETH 5-day momentum with 0.5x/2.0x exposure. Reused 2026
confirmation returned `+68.81%` with `-10.16%` drawdown but reached the 25% target in only `1/8`
months. Under `10+5 bps` stress costs it returned `+58.96%` and reached `0/8` target months.

The state overlay is rejected. The authoritative artifacts are
[`factor-overlay-20260815-121202-630588.md`](factor-overlay-20260815-121202-630588.md) and its
adjacent JSON file.
