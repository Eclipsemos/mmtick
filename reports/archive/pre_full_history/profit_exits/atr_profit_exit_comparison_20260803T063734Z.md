# ATR Profit Exit Comparison

Generated: 2026-08-03T06:37:34.628922+00:00
Shared data cutoff: 2026-08-03T14:37:21.994000+08:00

All variants use ATR(21) x 4 entries and base trailing stop. Orders fill on the next stored Tick with configured Taker fees, slippage, leverage and funding.

- `baseline`: current strategy, no additional take profit.
- `fixed_6atr`: exit after a favorable move of 6 x entry ATR.
- `protect_2atr_trail_2_5atr`: after a 2 x entry ATR favorable move, activate a one-way 2.5 x current ATR profit stop.

Profit exits flatten the position and do not reverse it. Re-entry still requires the production strategy's normal signal rules.

## SOXLBUSDT (spot)

Range: 2026-07-30T19:15:11.829000+08:00 to 2026-08-03T14:37:21.994000+08:00; 413,684 stored ticks / 499,908 underlying trades.

Data continuity: 19 trade-ID gaps, 1,927 missing trade IDs. Execution: 1x leverage, 1.00x target exposure, 10.0 bps fee + 5.0 bps slippage per fill.

| Variant | Net return | Net PnL | Final equity | Trades | Win rate | Profit factor | Max DD | Fees | Funding | Profit exits | End |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `baseline` | 27.41% | 27,410.95 | 127,410.95 | 3 | 66.67% | 25.87 | -6.72% | 726.06 | 0.00 | 0 | FLAT |
| `fixed_6atr` | 11.55% | 11,554.16 | 111,554.16 | 3 | 66.67% | 12.97 | -3.13% | 656.54 | 0.00 | 2 | FLAT |
| `protect_2atr_trail_2_5atr` | 20.55% | 20,550.89 | 120,550.89 | 3 | 66.67% | 25.32 | -5.52% | 689.10 | 0.00 | 3 | FLAT |

Highest net profit in this sample: `baseline` at 27,410.95 (27.41%).

## SOXLUSDT (futures)

Range: 2026-07-31T10:59:58.745000+08:00 to 2026-08-03T14:37:21.994000+08:00; 359,390 stored ticks / 6,534,681 underlying trades.

Data continuity: 1,632 trade-ID gaps, 191,078 missing trade IDs. Execution: 2x leverage, 1.25x target exposure, 5.0 bps fee + 2.0 bps slippage per fill.

| Variant | Net return | Net PnL | Final equity | Trades | Win rate | Profit factor | Max DD | Fees | Funding | Profit exits | End |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `baseline` | 20.31% | 20,310.83 | 120,310.83 | 4 | 75.00% | 15.50 | -9.32% | 626.56 | -168.36 | 0 | SHORT |
| `fixed_6atr` | 10.74% | 10,742.95 | 110,742.95 | 4 | 50.00% | 3.19 | -6.79% | 628.69 | 95.32 | 2 | SHORT |
| `protect_2atr_trail_2_5atr` | 6.87% | 6,872.52 | 106,872.52 | 4 | 50.00% | 2.88 | -6.07% | 531.13 | -295.75 | 3 | FLAT |

Highest net profit in this sample: `baseline` at 20,310.83 (20.31%).

## Combined Accounts

Each instrument starts with an independent 100,000 USDT account.

| Variant | Combined net PnL | Combined final equity |
|---|---:|---:|
| `baseline` | 47,721.79 | 247,721.79 |
| `fixed_6atr` | 22,297.11 | 222,297.11 |
| `protect_2atr_trail_2_5atr` | 27,423.41 | 227,423.41 |

## Limitations

The sample spans only a few days and is not an out-of-sample validation. Open positions are marked to the final Tick, so net PnL includes unrealized PnL. Futures records are 250 ms buckets; intrabucket price paths are unavailable. Trade-ID gaps identify warehouse outages and can change simulated signals.
