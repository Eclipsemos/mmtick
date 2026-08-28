# SOXL Lower-Leverage Outperformance Candidate

The current live baseline is 2x leverage x 62.5% allocation (1.25x exposure):
`+157.63%` full-history return and `-26.71%` maximum drawdown. The test used live-startup
replay, ATR(32)x3, 5 bps fees, 2 bps slippage, and recorded funding.

The proposed 5% margin x20 is 1.0x exposure and returned `+119.99% / -21.98%`, so it is
safer but does not beat live. A 5x plan with 27% margin (1.35x exposure) returned
`+173.32% / -28.53%`, beat live in the full, train, validation, and holdout windows, and
had zero liquidations in the approximate Binance-rate stress replay. A 5x x28% plan returned
`+181.27% / -29.42%`, but leaves only 0.58 percentage points of DD buffer.

The 5x x27% plan is the best candidate for continued paper observation. It slightly
underperformed live in the recent Aug 9-26 window (`-5.68%` vs `-5.23%`) and therefore is
not production-approved. Use no fixed profit target for this candidate. The 1.5 ATR target
reduced full-period performance and breached the 30% DD gate at exposures above 1.0x.

At 5x, public SOXLUSDT maintenance margin of 2.5% leaves an approximate 17.5% adverse-price
distance before liquidation, compared with roughly 2.5% at 20x. These are not exact Binance
mark-price brackets; an exchange-accurate simulator and hosted stop are still required.

Reproduce with:

```bash
PYTHONPATH=src python3 scripts/research/optimize_soxl_strategy.py \
  --grid lower-leverage-liquidation \
  --splits full,train,validation,holdout,august9_to_now \
  --live-startup --workers 8 \
  --output reports/experiments/soxl_atr/lower-leverage-liquidation-2026-08-26.json
```
