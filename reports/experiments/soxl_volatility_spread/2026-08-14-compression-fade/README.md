# SOXLUSDT Compression-Fade Volatility-Spread Exploration

Status: **exploratory_post_reveal_no_clean_holdout**
Selected variant: `long_only/8-32/entry_0.75/exit_1.2`
Fresh holdout used for selection: **no**

Compression-fade enters against a compressed channel boundary and exits at the prior mean, volatility expansion, a stop, or maximum hold. Signals use only prior bars.

| Path | Train geo/day | Validation geo/day | Confirmation geo/day | Development geo/day | Fresh return | Trades |
|---|---:|---:|---:|---:|---:|---:|
| Compression fade | +0.16% | +0.21% | +0.10% | +0.17% | -1.73% | 40 |
| Frozen breakout | +1.42% | +1.08% | +0.60% | +1.21% | +0.74% | 64 |

Train-selected combination: `fade_0.0`, correlation `-0.017`.

| Total exposure | Development geo/day | Development DD | Fresh return |
|---:|---:|---:|---:|
| 1.25x | +1.22% | -6.98% | +0.74% |
| 2.00x | +1.86% | -11.09% | +1.10% |
| 3.00x | +2.60% | -16.49% | +1.48% |
| 5.00x | +3.81% | -27.00% | +1.93% |
| 7.50x | +4.86% | -39.58% | +1.92% |
| 10.00x | +5.46% | -51.55% | +1.30% |

The compression-fade result is not a production recommendation. The 5% target is unmet. Daily linear scaling may cross 5% in development but is not an executable shared-margin replay and does not change that conclusion.
