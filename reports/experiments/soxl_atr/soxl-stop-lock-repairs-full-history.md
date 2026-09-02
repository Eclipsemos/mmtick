# SOXLUSDT ATR Stop-Exit Lock Repair Backtest

- Generated: `2026-09-02T05:49:53.219390+00:00`
- Replay: `2026-05-17T16:00:00+00:00` to `2026-08-31T23:59:59.991000+00:00`
- Data: 12,508,261 stored ticks / 225,979,560 underlying trades; 200 warmup bars.
- Frozen strategy: ATR(32) x 3, long-only, live-startup, 2x leverage x 62.5% allocation.
- Costs: 5 bps fee and 2 bps slippage per fill; Funding included.

## Policies

- `baseline`: current one-action-per-bar lock; a stop crossing during the lock is discarded.
- `bypass_action_lock`: a reduce-only ATR stop exit may signal even on the entry bar; fill remains next Tick.
- `latch_next_bar`: a stop crossing during the lock is remembered and signaled on the first eligible Tick of the next bar.

## Results

| Policy | Net PnL | Return | Delta vs baseline | Max DD | Trades | Wins | Win rate | Profit factor | Fees | Funding | All signals | Profit/latched exits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | $149,912.38 | 149.91% | +0.00% | -26.69% | 161 | 65 | 40.37% | 1.359 | $38,102.55 | $-2,581.05 | 161 | 0 |
| bypass_action_lock | $112,333.93 | 112.33% | -37.58% | -26.69% | 165 | 65 | 39.39% | 1.292 | $34,439.08 | $-2,403.82 | 165 | 0 |
| latch_next_bar | $126,890.22 | 126.89% | -23.02% | -26.69% | 163 | 66 | 40.49% | 1.332 | $34,861.58 | $-2,429.82 | 173 | 10 |

## Interpretation

This is a full local-history replay, not an out-of-sample guarantee. The two repair policies change only stop-exit timing; all candidates use the same ticks, warmup, costs, Funding, and next-Tick execution model. The latch policy is intentionally distinct from bypass: it waits for the next bar after a locked crossing.
