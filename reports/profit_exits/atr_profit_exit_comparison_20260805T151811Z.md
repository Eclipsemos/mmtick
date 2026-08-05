# ATR Profit Exit Comparison

Generated: 2026-08-05T15:18:11.471379+00:00
Shared data cutoff: 2026-08-05T23:16:51.708000+08:00

All variants use ATR(21) x 4 entries and base trailing stop. Orders fill on the next stored Tick with configured Taker fees, slippage, leverage and funding.

- `baseline`: current strategy, no additional take profit.
- `protect_1atr_trail_0_5atr`: after a 1 x entry ATR favorable move, activate a one-way 0.5 x current ATR profit stop (aggressive protection).
- `protect_2atr_trail_0_5atr`: after a 2 x entry ATR favorable move, activate a one-way 0.5 x current ATR profit stop (drawdown-focused candidate).

Profit exits flatten the position and do not reverse it. Re-entry still requires the production strategy's normal signal rules.

## SOXLUSDT (futures)

Range: 2026-07-31T10:59:58.745000+08:00 to 2026-08-05T23:16:51.708000+08:00; 858,294 stored ticks / 17,547,026 underlying trades.

Data continuity: 4,338 trade-ID gaps, 328,423 missing trade IDs. Execution: 2x leverage, 1.25x target exposure, 5.0 bps fee + 2.0 bps slippage per fill.

| Variant | Net return | Net PnL | Final equity | Trades | Win rate | Profit factor | Max DD | Fees | Funding | Profit exits | End |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `baseline` | 33.75% | 33,749.02 | 133,749.02 | 12 | 50.00% | 2.23 | -19.05% | 1,943.73 | -187.45 | 0 | FLAT |
| `protect_1atr_trail_0_5atr` | 11.08% | 11,084.75 | 111,084.75 | 10 | 90.00% | 4.60 | -4.21% | 1,302.11 | 0.00 | 9 | FLAT |
| `protect_2atr_trail_0_5atr` | 24.99% | 24,989.28 | 124,989.28 | 10 | 90.00% | 9.00 | -4.44% | 1,366.82 | 0.00 | 9 | FLAT |

Highest net profit in this sample: `baseline` at 33,749.02 (33.75%).

## Combined Accounts

Each instrument starts with an independent 100,000 USDT account.

| Variant | Combined net PnL | Combined final equity |
|---|---:|---:|
| `baseline` | 33,749.02 | 133,749.02 |
| `protect_1atr_trail_0_5atr` | 11,084.75 | 111,084.75 |
| `protect_2atr_trail_0_5atr` | 24,989.28 | 124,989.28 |

## Limitations

The sample spans only a few days and is not an out-of-sample validation. Open positions are marked to the final Tick, so net PnL includes unrealized PnL. Futures records are 250 ms buckets; intrabucket price paths are unavailable. Trade-ID gaps identify warehouse outages and can change simulated signals.
