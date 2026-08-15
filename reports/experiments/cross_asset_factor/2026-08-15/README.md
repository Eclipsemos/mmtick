# BTC/ETH cross-asset factor study

This research-only study tests common market regime, dual trend, leader rotation, relative value,
relative reversion, and causally adaptive relative-value factors on BTCUSDT and ETHUSDT. Signals
use closed 4h or daily bars and fill at the next open. Fees, slippage, and historical funding for
both legs are included.

Candidate and exposure selection use 2021-2025 only. The fixed result is then replayed on
2026-01-01 through 2026-08-10. The 2026 split is isolated from this search, but it has already been
seen in earlier project research and therefore is not a fresh holdout.

## Result

The final search evaluated 3,960 candidates. Two passed the development eligibility rules.

| Candidate type | Development 1x | 2026 confirmation 1x | Confirmation DD |
|---|---:|---:|---:|
| Static relative-value winner | +143.35% | -15.03% | -15.63% |
| Adaptive efficacy, 126d | +99.73% | -6.83% | -9.77% |

The static winner was positive in every development year, but its direction inverted in 2026 and
lost money in all eight confirmation months. Development selected 2x exposure, which changed the
confirmation result to -28.28% and -29.29% drawdown. The 10+5 bps stress result was -34.29%.

A diagnostic equal-weight blend of both development-eligible factors returned -11.55% at 1x in
2026. It reduced the static winner's loss but did not establish a profitable edge. Static relative
reversal was added only after the first confirmation result and cannot be treated as new
confirmation evidence.

A preliminary run incorrectly excluded zero-return bars from realized-volatility samples. The
committed report includes those bars, adds a gross-weight invariant, and is authoritative; the
preliminary reports were removed rather than retained as evidence.

No candidate or ensemble reached a 25% month. This factor family is rejected for trading and
leverage; future work should move to a genuinely different data hypothesis rather than expand this
parameter neighborhood.

## Reports

- The latest `cross-asset-factor-*.json` and `.md` pair is the authoritative static, reversion,
  adaptive, exposure, stress, and consensus report. Earlier sequential runs are summarized above
  and can be regenerated from the committed script.
