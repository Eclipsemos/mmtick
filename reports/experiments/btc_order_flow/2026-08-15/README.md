# BTC order-flow factor study

This research-only study tests whether archived aggregate-trade flow adds a stable BTCUSDT
signal beyond closed-bar price factors. Signals use closed 4h bars and execute at the next 4h
open. Selection uses 2024 training and 2025 validation only; 2026-01-01 through 2026-08-10 is an
untouched confirmation split. Base costs are 5 bps fee plus 2 bps slippage per fill, with a
10+5 bps stress run and historical funding.

## Direction-source comparison

The archive has Binance aggressor-side direction for only 22.11% of notional. Earlier exploratory
runs tried assigning unknown buckets from their close versus open price. The final implementation
does not merge that estimate into the reported direction. It evaluates two independent sources:

- `reported`: only buckets with Binance's `buyer_is_maker` field.
- `tick_rule`: all buckets classified only by bucket close versus bucket open; unchanged buckets
  remain unclassified.

| Source | Train | Validation | Confirmation | Confirmation DD | Stress confirmation | 25% confirmation months |
|---|---:|---:|---:|---:|---:|---:|
| Reported | +13.27% | +7.53% | +13.35% | -12.92% | +8.39% | 0% |
| Tick rule | +24.86% | +1.50% | -14.77% | -18.43% | -18.77% | 0% |

The reported-direction candidate is the stronger result, but it produced only +13.35% over the
entire confirmation period rather than 25% per month. Its best confirmation month was +11.35%.
The tick-rule candidate failed out of sample. Neither source is approved for trading or leverage.

## Reports

- `btc-order-flow-20260815-081937-849257`: final 1,280-candidate independent-source comparison.
- `btc-order-flow-20260815-073324-439992`: exploratory reported-direction-only run.
- `btc-order-flow-20260815-080310-198436`: invalidated exploratory run that merged inferred
  unknown-bucket direction into reported direction. It is retained as negative evidence.

Generated caches live under `data/order_flow_cache/` and are intentionally excluded from Git.
