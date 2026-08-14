# SOXLUSDT Five-Minute Volatility-Spread Exploration

Status: **rejected_no_train_validation_candidate**
Best rejected parameters: `compression_release/36-192/entry_1.1/exit_1/breakout_48/stop_2/hold_144`
5% daily target: **not achieved**

Five-minute bars are constructed from persisted 250ms aggregate trades. Signals use closed bars and the order fills at the first persisted Tick in the subsequent 5m bar. No-trade intervals are flat carry bars and cannot fill an order.

| Path | Train geo/day | Validation geo/day | Confirmation geo/day | Development geo/day | Revealed 8/11-13 | 5m close DD |
|---|---:|---:|---:|---:|---:|---:|
| 5m spread | -0.19% | -0.21% | +0.02% | -0.17% | -2.18% | -26.99% |
| Frozen 15m | +1.42% | +1.08% | +0.60% | +1.21% | +0.74% | -11.11% |

The search tested 192 candidates; 0 passed train/validation and 0 of 20 finalists were positive in confirmation.

This is not a production recommendation. August 11-13 was already revealed and is diagnostic only; the current 5m grid is rejected before forward monitoring.
