# BTC/ETH Factor Portfolio

This experiment asks whether a second causal BTC or ETH factor can diversify the strongest
BTC-shock-to-ETH lead-lag factor enough to raise leverage while retaining the 35% drawdown gate.

The fixed universe contains 2,988 candidates:

- causal expression factors on 1h and 4h bars;
- EMA, Donchian, momentum, RSI, MACD, and Bollinger factors on 1h, 4h, and daily bars;
- BTC and ETH shock continuation/reversal event factors, including ETH-to-BTC response.

Every sleeve is replayed independently with next-open fills, historical funding, 5 bps taker fees,
and 2 bps slippage per fill. The portfolio uses fixed initial sleeve capital rather than free daily
rebalancing. Selection uses 2021-2023 discovery and 2024-2025 validation only.

## Result

The experiment is rejected. Eighty-seven independent sleeves passed the two development intervals
and monthly-correlation screen. The selected portfolio retained 60% in the lead-lag sleeve and put
40% in a BTC 1h Donchian long-only sleeve at 1.5x portfolio leverage.

| Split | Return | Daily-close max DD | Positive months | 25% months |
|---|---:|---:|---:|---:|
| 2021-2023 discovery | +748.65% | -32.95% | 58.33% | 5/36 |
| 2024-2025 validation | +107.16% | -34.00% | 58.33% | 3/24 |
| 2026 reused confirmation | +84.27% | -15.18% | 75.00% | 2/8 |
| 2026 stressed costs | +76.45% | -15.77% | 75.00% | 2/8 |

The second sleeve lost 12.75% during 2026 and did not fill the lead-lag factor's weak months. The
portfolio still reached 25% only in February and June, so it did not improve the 2/8 coverage of
the strongest standalone factor. Adding the event-factor universe did not displace the Donchian
candidate selected before that expansion.

Portfolio drawdown is available only at daily closes; each component retains its own bar-level
drawdown. This limitation is another reason the result cannot be treated as approved risk evidence.

The authoritative artifacts are
[`factor-portfolio-20260815-093026-239733.md`](factor-portfolio-20260815-093026-239733.md)
and its adjacent JSON file.
