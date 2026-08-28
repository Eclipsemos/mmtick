# SOXL Equal-Exposure Leverage Observation

## Result

Leverage did not create or remove strategy edge in the replay. Holding target account
notional near 0.5x produced identical returns, drawdowns, trade counts, fees, and funding:

| Leverage | Margin fraction | Full-period return | Full-period DD | Aug 9-26 return | Aug 9-26 DD |
|---:|---:|---:|---:|---:|---:|
| 5x | 10% | +16.87% | -14.46% | +4.49% | -2.16% |
| 10x | 5% | +16.87% | -14.46% | +4.49% | -2.16% |
| 20x | 2.5% | +16.87% | -14.46% | +4.49% | -2.16% |

The same equality holds for no target and 1, 2, and 3 ATR targets. The replay sizes
notional as `equity * margin_fraction * leverage`; only the collateral representation
changes.

## Risk Interpretation

At the same 0.5x account exposure, lower leverage gives more adverse-price room before
maintenance margin: approximately 20% at 5x, 10% at 10x, and 5% at 20x, before exchange
maintenance requirements, fees, and liquidation slippage. These are rough geometric limits,
not Binance liquidation prices.

For the 1.5 ATR target, the worst closed-trade loss was 3.16% of starting equity in every
leverage representation. Relative to entry margin it was approximately 31.1% at 5x, 62.3%
at 10x, and 124.6% at 20x. The 20x replay therefore contains two closed losses larger than
posted entry margin; a real isolated position could have liquidated first.

## Recommendation

Use leverage only as an implementation choice. If account notional is intended to remain
0.5x, prefer 5x or 10x over 20x until an exchange-accurate liquidation and stop-fill model
is added. Continue forward observation of 1% and 2.5% margin budgets; do not increase margin
to chase the linearly scaled returns.

## Reproduction

```bash
PYTHONPATH=src python3 scripts/research/optimize_soxl_strategy.py \
  --grid leverage-equivalent \
  --splits full,train,validation,holdout,august9_to_now \
  --live-startup --workers 8 \
  --output reports/experiments/soxl_atr/leverage-equivalent-2026-08-26.json
```
