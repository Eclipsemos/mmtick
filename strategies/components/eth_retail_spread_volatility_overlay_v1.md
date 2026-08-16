# ETH Retail-Spread Volatility Overlay v1

Status: `provisional_forward_candidate`. This is the frozen market-state sleeve used by
`btc_eth_expanding_calendar_router_v1`; it is not approved for live trading on its own.

- Baseline: 4x internal BTC/ETH four-factor event anchor.
- State: ETH top-position/global-account log-ratio z-score over 540 closed 4h bars.
- Exposure: 2.0x at z-score <= -1.25, 0.8x otherwise, and 1.0x when unavailable.
- Volatility: trailing 20-day RMS targeting 3% daily, clamped to 0.6x-1.1x.
- Timing: last complete prior UTC-day snapshot; rebalance daily.
- Costs: 5/2 bps base and 10/5 bps stress fee/slippage, plus 7/15 bps turnover.

The complete machine-readable parameters are in the adjacent JSON definition.
