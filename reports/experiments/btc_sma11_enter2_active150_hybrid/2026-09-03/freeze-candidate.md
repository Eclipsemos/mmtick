# BTC SMA11/40 Hybrid Strict-15m Forward Freeze

This is a shadow/forward observation freeze, not approval for live trading.

## Frozen Rule

- Symbol: `BTCUSDT`.
- Signal: completed UTC daily candle only; no incomplete daily bar may update state.
- Trend: SMA `11/40`; a bearish day requires both close and SMA11 below SMA40.
- State: after 2 consecutive bearish days target `0X`; after 1 non-bearish day target `1.5X`.
- Execution: Binance spot daily before 2020; for forward observation, USD-M target changes execute at
  the next 15m open.
- Capital model: 50% spot and 50% isolated USD-M collateral.
- Costs: 10 bps fee plus 5 bps slippage per side; historical Funding in the backtest.
- Guard: 2X futures opening control and an effective-leverage hard limit of `3X`.

## Freeze Boundary

- Historical endpoint: `2026-09-03T01:14:59.999Z`.
- Audit: [strict hybrid report](README.md).
- Neighborhood: 8 of 30 nearby configurations passed Research/Validation default and moderate-cost
  checks, default-cost OOS, and the 3X constraint.

## Forward Protocol

Do not change SMA periods, confirmation counts, target exposure, cost assumptions, or leverage
control during the observation. Record each target change, actual or modeled fill, Funding,
effective leverage, daily strategy return, and matched BTC B&H return. Report 30-day, 90-day, and
180-day excess separately. A parameter change starts a new candidate and a new freeze.

Run `scripts/research/audit_btc_sma11_hybrid_forward.py` after each data refresh. Its ledger starts
at `2026-09-03T01:15:00Z`, so it cannot accidentally include the final bar used for selection.

## Promotion Gates

Maintain `<=3X` effective leverage with no liquidation; retain positive net excess after actual
costs over a meaningful forward sample; and investigate any rolling 90-day underperformance or
drawdown above the historical risk budget. The historical `-75.62%` drawdown prevents live approval
until an explicit capital-risk policy is separately validated.
