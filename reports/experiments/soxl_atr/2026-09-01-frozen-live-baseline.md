# SOXLUSDT Frozen Live-Strategy Baseline — 2026-09-01

## Purpose

This snapshot freezes the research state before testing ATR stop-exit lock
repairs. It is the comparison baseline; no repair behavior is included.

## Frozen Strategy

- Symbol/instrument: `SOXLUSDT` / `soxl_perp`
- Direction: long-only
- Execution: 15-minute Tick ATR, live-startup alignment
- ATR: period `32`, multiplier `3.0`
- Venue model: 2x isolated leverage, 62.5% equity allocation (1.25x target exposure)
- Profit takers: disabled (no gross TP, static Net TP, dynamic Net TP, or ATR profit protection)
- Costs: 5 bps fee and 2 bps slippage per fill; recorded Funding included
- Action policy: existing one-action-per-15-minute-bar lock

## Data Snapshot

- Database: `data/paper.db`
- Complete August replay range: 2026-08-01 00:00:00 UTC through
  2026-08-31 23:59:59.999 UTC (2026-09-01 07:59:59.999 Beijing time)
- SOXL aggregate ticks: 3,603,240 buckets, through
  2026-08-31 23:59:59.991 UTC
- Closed 15-minute bars: through 2026-08-31 23:59:59.999 UTC
- Funding: through 2026-08-31 16:00:00 UTC

## Baseline Evidence

- Full August report: [2026-08-trades-live-startup-beijing-final.md](2026-08-trades-live-startup-beijing-final.md)
- August baseline result: 46 completed rounds, 16 wins, 34.78% win rate,
  +$7,614.72 net PnL, +7.61% account return, -17.64% maximum drawdown.
- Repository HEAD when frozen: `1452973` (`fix: reserve futures replay entry fees`).
- The worktree contained pre-existing uncommitted research and data files;
  this document records the state without overwriting or removing them.

The baseline is for controlled research only. It does not authorize live order
submission or imply that historical replay results are forward performance.
