# BTC to ETH Lead-Lag Factor

This experiment tests whether an unusually large closed 4h BTC return predicts a delayed ETH
response. Signals are normalized causally, filled at the next ETH 4h open, and replayed with
historical ETH funding, 5 bps taker fees, and 2 bps slippage per fill.

The search evaluated 960 factor candidates on 2021-2025 development data. Candidate dimensions
included normalization window, shock threshold, holding period, direction, and ETH response gate.
The selected base factor was:

`btc-shock-eth-long_short-window-15d-threshold-2-hold-12x4h-gate-underreaction`

## Result

This is the strongest factor found in the project so far, but it does not meet the stated goal and
is not approved for trading.

| Replay | Development return | Confirmation return | Confirmation max DD | 25% confirmation months |
|---|---:|---:|---:|---:|
| Base factor, 1.0x | +632.81% | +63.33% | -13.19% | 0/8 |
| Development-selected dynamic sizing | +1,750.44% | +102.13% | -16.38% | 2/8 |
| Dynamic sizing, stressed costs | - | +94.83% | -16.91% | 2/8 |

Dynamic sizing uses 0.5x exposure for moderate shocks, 1.5x for strong shocks, and 2.0x for
extreme shocks, with a 15% calendar-month loss limit. These settings were selected using only the
development interval.

The target requires at least half of confirmation months to return 25% or more while respecting
the 35% drawdown gate. The strongest replay reached that return in February and June only, so its
25% month coverage is 25%, below the required 50%.

## Evidence Status

- Development: 2021-01-01 through 2025-12-31 UTC.
- Confirmation diagnostic: 2026-01-01 through 2026-08-10 UTC.
- The 2026 interval was excluded from this search's parameter selection, but it has already been
  viewed in earlier project studies. It is a reused holdout, not fresh independent confirmation.
- The study uses synchronized 4h OHLCV bars, not sub-second cross-exchange event data.
- Liquidation, market impact, exchange failure, and shared-margin effects are not modeled.

The authoritative artifacts are
[`btc-eth-lead-lag-20260815-090818-243225.md`](btc-eth-lead-lag-20260815-090818-243225.md)
and its adjacent JSON file.
