# SOXLUSDT Accelerating Volatility-Spread Exploration

Status: **rejected_acceleration_gate_not_selected**
Best gate: `none`
Best parameters: `12-64/entry_1.1/exit_0.8/breakout_24/stop_2.5/hold_48`
5% daily target: **not achieved**

The entry gate uses only the latest and preceding closed 15m volatility-spread ratios. It admits a breakout only when the spread is accelerating or first crosses the configured level.

The stable selection chose `none`, so the acceleration and crossing gates add no value over the frozen baseline and are rejected.

| Path | Train geo/day | Validation geo/day | Confirmation geo/day | Development geo/day | Revealed 8/11-13 | 15m close DD |
|---|---:|---:|---:|---:|---:|---:|
| Acceleration gate | +1.42% | +1.08% | +0.60% | +1.21% | +0.74% | -11.11% |
| Frozen baseline | +1.42% | +1.08% | +0.60% | +1.21% | +0.74% | -11.11% |

The search tested 480 candidates; 248 passed train/validation and 8 of 20 finalists were positive in confirmation.

This is not a production recommendation. August 11-13 was already revealed and is diagnostic only; a retained candidate would require a fresh post-August-13 window.
