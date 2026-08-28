# SOXL Live-Baseline Outperformance Sweep

## Acceptance Gate

Candidate must exceed the current live-equivalent baseline return while keeping maximum
drawdown below 30%. Baseline is long-only ATR(32)x3, no fixed profit target, 2x leverage,
62.5% allocation (1.25x account exposure), live-startup replay.

Baseline replay:

| Window | Return | Max DD |
|---|---:|---:|
| Full May 17-Aug 26 | +157.63% | -26.71% |
| Train May 17-Jun 30 | +84.98% | -20.65% |
| Validation July | +26.43% | -26.71% |
| Holdout Jul 31-Aug 26 | +12.94% | -14.34% |
| Aug 9-Aug 26 | -5.23% | -13.87% |

## Exposure Sweep

| Plan | Effective exposure | Full return / DD | Train return / DD | Validation return / DD | Holdout return / DD | Aug 9 return / DD |
|---|---:|---:|---:|---:|---:|---:|
| 5% margin x20 | 1.00x | +119.99% / -21.98% | +65.96% / -16.91% | +22.22% / -21.98% | +10.75% / -11.72% | -4.14% / -11.24% |
| 6.25% margin x20 | 1.25x | +157.63% / -26.71% | +84.98% / -20.65% | +26.43% / -26.71% | +12.94% / -14.34% | -5.23% / -13.87% |
| 6.75% margin x20 | 1.35x | +173.32% / -28.53% | +92.80% / -22.10% | +27.94% / -28.53% | +13.76% / -15.40% | -5.68% / -14.90% |
| 7.0% margin x20 | 1.40x | +181.27% / -29.42% | +96.76% / -22.82% | +28.66% / -29.42% | +14.15% / -15.93% | -5.90% / -15.41% |
| 7.5% margin x20 | 1.50x | +197.39% / -31.18% | +104.75% / -24.23% | +30.03% / -31.18% | +14.92% / -16.98% | -6.35% / -16.42% |

These rows have no fixed profit-taker. Adding 1-3 ATR targets reduced full-history returns;
the best 1.5 ATR candidate at 1.0x exposure returned +33.84%, far below the +157.63% live
baseline. Profit targets improved August results but did not preserve the long-sample edge.

## Conclusion

`5% x20` is safer by drawdown but fails the outperformance requirement. `7.5% x20` fails
the DD gate. `6.75%-7.0% x20` passes mathematically on this historical sample, but the
margin is too narrow: 7.0% leaves only 0.58 percentage points below the 30% DD limit and
performs worse than live in the Aug 9-26 forward window. It is a research candidate, not a
production approval.

The most defensible next paper setting is the live baseline exposure (1.25x) or at most
1.35x, with no fixed target. Before increasing exposure, require an exchange-accurate
liquidation replay, a hard stop, and a longer independent forward sample. Leverage should
be chosen for liquidation headroom; equivalent exposure at 5x or 10x is preferable to 20x.

## Lower-Leverage Stress Result

The same boundary was replayed with the public SOXLUSDT maintenance rate (2.5%) and
liquidation fee (1.5%), using approximate isolated liquidation logic. A 5x plan had no
liquidations in any tested window:

| Plan | Exposure | Full return / DD | Train return / DD | Validation return / DD | Holdout return / DD | Aug 9 return / DD |
|---|---:|---:|---:|---:|---:|---:|
| 5x x20% margin | 1.00x | +119.99% / -21.98% | +65.96% / -16.91% | +22.22% / -21.98% | +10.75% / -11.72% | -4.14% / -11.24% |
| 5x x25% margin | 1.25x | +157.63% / -26.71% | +84.98% / -20.65% | +26.43% / -26.71% | +12.94% / -14.34% | -5.23% / -13.87% |
| 5x x27% margin | 1.35x | +173.32% / -28.53% | +92.80% / -22.10% | +27.94% / -28.53% | +13.76% / -15.40% | -5.68% / -14.90% |
| 5x x28% margin | 1.40x | +181.27% / -29.42% | +96.76% / -22.82% | +28.66% / -29.42% | +14.15% / -15.93% | -5.90% / -15.41% |

This is preferable to the corresponding 20x plans because the approximate liquidation price
distance is about 17.5% at 5x, versus 2.5% at 20x. The 5x x27% plan is the best research
candidate: it clears the historical 30% gate with 1.47 points of buffer and beats the live
baseline outside the recent forward window. It is not yet approved because the recent window
is slightly worse and the liquidation model is still approximate.

## Reproduction

```bash
PYTHONPATH=src python3 scripts/research/optimize_soxl_strategy.py \
  --grid live-outperformance \
  --splits full,train,validation,holdout,august9_to_now \
  --live-startup --workers 8 \
  --output reports/experiments/soxl_atr/live-outperformance-30pct-dd-2026-08-26.json
```
