# Event Meta-Label Factor

This experiment uses a three-seed GPU XGBoost ensemble to decide whether each causal 4h BTC shock
should trade the delayed ETH response. Features include only information known at the signal bar:
multi-horizon BTC/ETH returns, causal shock scores, volatility, trend efficiency, range, close
location, volume surprise, and rolling cross-asset correlation.

The chronology is fixed:

- 2021-2022: model fitting;
- 2023: boosting early stopping;
- 2024-2025: probability, exposure, and monthly loss-limit selection;
- 2026 through August 10: reused confirmation diagnostic.

## Result

The experiment is rejected. Predictive performance did not transfer across years.

| Split | Events | ROC AUC | Average precision |
|---|---:|---:|---:|
| 2021-2022 model train | 111 | 0.813 | 0.813 |
| 2023 early stop | 54 | 0.612 | 0.624 |
| 2024-2025 selection | 137 | 0.473 | 0.503 |
| 2026 reused confirmation | 31 | 0.496 | 0.626 |

None of the 196 probability, exposure, and monthly loss-limit configurations passed the
development selection gates. The reported 0.50 probability, 4x exposure, and 15% loss-limit row is
the highest-ranked failed fallback, not a selected candidate. It lost 84.28% in 2024-2025.

The fallback confirmation result was -2.40% with -44.59% max drawdown. It reached 25% in January
and March only, and stressed costs reduced the result to -10.35%. The near-random selection and
confirmation AUC values show that the apparent in-sample classification edge is not stable.

The authoritative artifacts are
[`event-meta-factor-20260815-093932-961425.md`](event-meta-factor-20260815-093932-961425.md)
and its adjacent JSON file. Model checkpoints remain under `data/event_meta_models/` and are not
committed.
