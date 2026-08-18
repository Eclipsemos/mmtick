# BTC/ETH 4h Reversal Factor Forward Lock

This file freezes two research-only relationships found by the single-factor stability study:

- BTC: fade the prior six 4h BTC returns when ranking the next 4h BTC return.
- ETH: fade the prior six 4h BTC returns when ranking the next 4h ETH return.

The evidence lock is `2026-08-11 UTC`; forward evidence begins `2026-08-12 UTC`. The monitor uses
5 bps fees and 2 bps slippage per fill, realized funding, and no parameter search. A review requires
30 complete UTC days and at least 150 non-overlapping observations. Cost-adjusted IC must be at
least `0.02` and retain at least half of its 2022-2025 development value.

This is an IC observation protocol, not an executable strategy or trading approval.

Run the deterministic monitor after updating complete UTC-day bars:

```bash
/home/spaceaic/env/.venv/bin/python scripts/research/monitor_factor_stability_forward.py
```
