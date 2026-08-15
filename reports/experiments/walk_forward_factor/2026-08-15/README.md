# Walk-Forward GPU Factor And Static Hybrid

This research-only experiment refreshes the three-seed XGBoost ensemble annually. Each prediction
year uses an expanding fit window and only the preceding year for early stopping and score
calibration. Signal controls, the BTC model used by the hybrid, outer allocation, and leverage are
all selected using 2024 and 2025. The reused 2026 interval remains confirmation-only.

The model-only BTC/ETH portfolio failed confirmation with `-48.41%` and `-49.24%` drawdown. A
joint search then evaluated all 43 development-risk-eligible BTC configurations alongside the
previously frozen four-sleeve static event anchor. It selected 60% anchor and 40% BTC at 1.25x.

| Split | Return | Max DD | 25% months |
|---|---:|---:|---:|
| 2024 selection | +125.22% | -25.41% | 3/12 |
| 2025 selection | +31.81% | -34.02% | 1/12 |
| 2026 reused confirmation | +128.07% | -16.12% | 3/8 |
| 2026 stress 10+5 bps | +104.71% | -19.99% | 3/8 |

The hybrid is rejected. It preserved positive stressed return and acceptable drawdown but did not
improve monthly target coverage over the static anchor. The authoritative artifacts are
[`walk-forward-factor-20260815-114051-794996.md`](walk-forward-factor-20260815-114051-794996.md)
and its adjacent JSON file. The final JSON also retains the model-only diagnostic; neither path is
an approved candidate.
