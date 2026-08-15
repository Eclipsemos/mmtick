# volatility-guarded-trend-20260815

Volatility-guarded static BTC trend complement around the frozen monthly-robust baseline.

Decision: `reused_confirmation_candidate_post_confirmation_refinement`. Trading approval: `false`.

## Search Result

- Coarse development-risk-eligible controls: `720`.
- Local development-risk-eligible controls: `134`.
- Best reused-confirmation coverage: `7/7`.
- Strict base-and-stress 7/7 configurations: `5`.

The local post-confirmation neighborhood contains base-and-stress 7/7 reused-confirmation configurations, but the local parameters were refined after observing 2026 and cannot be treated as an unbiased strategy-selection result.
Partial `2026-08` is excluded from strict counts.

## Forward Freeze

Volatility `3d/60d/q0.25`; calm trend/baseline `55%/45%`; volatile `5%/95%`; leverage `8.00x`; monthly locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 29.24% | 26.48% |
| 2026-02 | 48.48% | 46.01% |
| 2026-03 | 33.29% | 30.52% |
| 2026-04 | 31.61% | 27.94% |
| 2026-05 | 17.55% | 15.55% |
| 2026-06 | 17.37% | 16.76% |
| 2026-07 | 22.35% | 15.71% |
| 2026-08 | -2.11% | -5.10% |

## Best Confirmation Diagnostic

Volatility `3d/60d/q0.25`; calm trend/baseline `55%/45%`; volatile `5%/95%`; leverage `8.00x`; monthly locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 29.24% | 26.48% |
| 2026-02 | 48.48% | 46.01% |
| 2026-03 | 33.29% | 30.52% |
| 2026-04 | 31.61% | 27.94% |
| 2026-05 | 17.55% | 15.55% |
| 2026-06 | 17.37% | 16.76% |
| 2026-07 | 22.35% | 15.71% |
| 2026-08 | -2.11% | -5.10% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The local parameter neighborhood was refined after observing confirmation failures.
- The trend candidate and baseline were selected in earlier development studies.
- Peak modeled outer leverage is 9x; liquidation and borrowing costs are not modeled.
- Partial August is shown diagnostically but excluded from the strict count.
