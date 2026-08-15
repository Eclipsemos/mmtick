# Extreme Funding-Event Factor

This research-only experiment z-scores each BTC or ETH funding settlement against the preceding
30, 90, or 180 events, then tests continuation and reversal holdings for 1-12 closed 4h bars. The
current event is excluded from its own normalization. Signals fill at the next 4h open and include
historical funding and explicit costs.

Fifty of 5,400 factor configurations passed development gates. A second-stage search evaluated
single and paired event sleeves beside the frozen static anchor. It selected BTC and ETH long-only
funding reversals at 13.33% and 6.67% portfolio weight, with 80% anchor weight and 1.5x outer
leverage.

The hybrid returned `+217.30%` with `-19.76%` drawdown in reused 2026 and `+179.39%` with `-22.81%`
drawdown under stress. It remained at `3/8` target months, so it is rejected despite improving
total return. The authoritative artifacts are
[`funding-event-factor-20260815-123440-309171.md`](funding-event-factor-20260815-123440-309171.md)
and its adjacent JSON file.
