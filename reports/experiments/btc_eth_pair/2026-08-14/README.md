# BTCUSDT/ETHUSDT Market-Neutral Pair Study

Generated: 2026-08-14T09:44:50.672591+00:00

BTC is the left leg and ETH the right leg. A +1 signal is long BTC/short ETH; -1 reverses the pair. Each leg receives half the gross exposure. Signals use closed daily K lines and fill at the next daily open.

| Family | Candidate | Train | Validation | Confirmation | Confirm DD | Trades |
|---|---|---:|---:|---:|---:|---:|
| ratio_ema_trend | `ratio-ema-50-200` | 10.40% | 10.43% | -4.77% | -10.53% | 2 |
| ratio_mean_reversion | `ratio-mean-120-1.5-0.5` | 0.70% | -24.21% | -1.19% | -4.54% | 2 |
| ratio_momentum | `ratio-momentum-120-0.1` | 4.11% | 33.57% | -4.52% | -7.35% | 11 |

## Selected Development Winner

Candidate: `ratio-ema-10-50`

Train 7.39%; validation 24.68%; confirmation 1.07%; geometric monthly confirmation 0.13%.

| Exposure | Confirmation return | Max DD | Bankrupt |
|---:|---:|---:|---|
| 0.5x | 0.57% | -3.07% | no |
| 1.0x | 1.07% | -6.00% | no |
| 2.0x | 1.90% | -11.51% | no |
| 3.0x | 2.47% | -16.59% | no |
| 4.0x | 2.81% | -21.29% | no |

## Decision

Status: `rejected_after_confirmation`.

The always-long-BTC/short-ETH benchmark returned 4.64% in confirmation.

This is OHLCV-level evidence: ETH aggregate trades were not imported, so it is not a Tick-level execution approval. The 25% monthly target remains unmet.
