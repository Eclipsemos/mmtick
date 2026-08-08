# Continuation Re-entry Threshold Study

Generated: 2026-08-06

## Rule under test

The production strategy remains unchanged. This study adds an optional, default-disabled
continuation re-entry rule to the replay engine:

- remember the actual fill price and direction when a position is completely closed;
- only on the next 15-minute bar, allow the original direction to re-enter;
- require price to exceed the exit fill by `N x current ATR` for a long, or fall below it by
  `N x current ATR` for a short;
- also require price to be on the correct side of the base ATR stop and pass the existing trend
  efficiency filter;
- expire the opportunity after that one bar;
- retain ATR(21) x 4 entries, 2 ATR profit-protection activation and a 0.5 current-ATR profit
  trail.

All orders use the next stored Tick, 2x leverage, 1.25x target exposure, 5 bps fees and 2 bps
slippage unless marked as a stress test.

## Updated full sample

Range: 2026-07-31 02:59:58 UTC to 2026-08-06 14:24:36 UTC; 1,054,439 stored ticks and
21,691,912 underlying trades.

| Re-entry threshold | Net return | Max DD | Completed trades | Wins | Re-entries | End |
|---:|---:|---:|---:|---:|---:|---|
| Disabled | 28.52% | -4.44% | 11 | 10 | 0 | FLAT |
| 1.2 ATR | 35.56% | -4.63% | 14 | 13 | 3 | FLAT |
| 1.3 ATR | 35.24% | -4.63% | 14 | 13 | 3 | FLAT |
| **1.4 ATR** | **36.93%** | **-4.51%** | 13 | 12 | 3 | LONG |
| 1.5 ATR | 36.62% | -4.51% | 13 | 12 | 3 | LONG |
| 1.6 ATR | 36.27% | -4.51% | 13 | 12 | 3 | LONG |
| 1.7 ATR | 34.40% | -5.50% | 12 | 11 | 2 | LONG |
| 1.8 ATR | 34.09% | -5.50% | 12 | 11 | 2 | LONG |

The 1.4-1.6 ATR region forms a stable local plateau. The 1.4 ATR threshold has the highest
marked-to-market return in the tested grid.

## Previously unseen sample

Range: 2026-08-05 16:15:35 UTC to 2026-08-06 14:24:36 UTC; 184,175 stored ticks and
3,871,176 underlying trades. This interval was not used to select the coarse threshold range.

| Re-entry threshold | Net return | Max DD | Completed trades | Re-entries | End |
|---:|---:|---:|---:|---:|---|
| Disabled | 2.82% | -1.49% | 1 | 0 | FLAT |
| 1.2 ATR | 6.33% | -1.57% | 2 | 1 | FLAT |
| 1.3 ATR | 6.09% | -1.57% | 2 | 1 | FLAT |
| **1.4 ATR** | **7.10%** | **-1.57%** | 1 | 1 | LONG |
| 1.5 ATR | 6.85% | -1.57% | 1 | 1 | LONG |
| 1.6 ATR | 6.63% | -1.71% | 1 | 1 | LONG |
| 1.7 ATR | 6.39% | -1.57% | 1 | 1 | LONG |
| 1.8 ATR | 6.14% | -1.57% | 1 | 1 | LONG |

The new sample contains exactly one qualifying continuation. Thresholds at 1.4 ATR and above
end with an open long, so their final return includes unrealized PnL.

## Cost stress

With fees doubled to 10 bps and slippage doubled to 4 bps:

| Range | Variant | Net return | Max DD | Fees | End |
|---|---|---:|---:|---:|---|
| New unseen | Disabled | 2.65% | -1.49% | 252.90 | FLAT |
| New unseen | 1.4 ATR | 6.82% | -1.57% | 381.21 | LONG |
| Full updated | Disabled | 26.10% | -4.78% | 3,022.21 | FLAT |
| Full updated | 1.4 ATR | 33.78% | -4.86% | 3,760.93 | LONG |

## Conclusion

`1.4 ATR` is the sample-optimal continuation threshold in this grid. It is preferable to the
nearby 1.5 ATR threshold on both the updated full sample and the previously unseen interval,
while producing essentially the same drawdown. Low thresholds from 0 to 1.0 ATR were rejected
because they caused excessive re-entry, fees and drawdown.

This is not yet sufficient evidence for automatic live activation: the full sample has only 13
completed trades, only three continuation entries, and the new sample has one continuation that
is still open at the cutoff. The recommended next step is a paper/shadow deployment at 1.4 ATR
until the open trade closes and more continuation events accumulate.
