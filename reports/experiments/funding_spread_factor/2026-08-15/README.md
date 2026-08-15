# BTC/ETH Funding-Spread Factor

This research-only experiment uses the trailing realized BTC-minus-ETH funding spread to form an
equal-notional pair. Carry mode shorts the higher-funding contract and buys the lower-funding
contract; crowding-follow mode takes the opposite price-direction hypothesis. Signals use closed
4h bars, fill at the next open, and include funding, fees, and slippage.

Ten of 1,440 pair configurations and 190 of 250 static-anchor hybrids passed 2021-2025 risk gates.
The selected 0.5x carry pair itself returned `-0.49%` with `-3.96%` drawdown in reused 2026. The
combined hybrid returned `+193.78%` with `-18.78%` drawdown under base costs and `+159.85%` under
stress, but both reached only `3/8` target months.

The factor is rejected. No spot basis history is available, so this result is realized-funding
relative value rather than cash-and-carry. The authoritative artifacts are
[`funding-spread-factor-20260815-122052-685141.md`](funding-spread-factor-20260815-122052-685141.md)
and its adjacent JSON file.
