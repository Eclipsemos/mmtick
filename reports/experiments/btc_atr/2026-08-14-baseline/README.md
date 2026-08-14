# BTCUSDT 15m ATR Baseline

Decision: rejected. No tested candidate supports the 25% monthly return target, and leverage was
not increased after the 1x candidates failed validation.

## Data

- Stored 250ms trade buckets: 232,816,089.
- Stored complete 15m bars: 91,584, with no gaps.
- Funding observations: 2,862.
- Coverage: 2024-01-01 00:00 UTC through 2026-08-11 23:59 UTC.
- Raw monthly archives: 31 files, 17,912,143,748 compressed bytes.

The replay included 5 bps taker fees per fill, 2 bps simulated slippage, actual stored funding,
1.0x target exposure, and 200 closed 15m bars of pre-replay warmup.

## Selection Protocol

- Training: 2026-05-17 through 2026-06-30.
- Validation: 2026-07-01 through 2026-07-31.
- Confirmation: 2026-08-01 through 2026-08-10.
- Grid: ATR periods 14, 21, and 28 crossed with multipliers 2.0, 2.5, and 3.0.
- Fixed controls: 15m bars, efficiency period 8, minimum efficiency 0.25, reversal confirmation
  0.25 ATR, long/short direction, no profit protection, and no continuation reentry.

The validation and confirmation intervals did not participate in training selection. The expanded
2024-2026 warehouse remains available for future hypotheses, but it was not searched after the
recent validation failure.

## Results

| Candidate | Training return | Training max DD | Training win rate | July return | Aug 1-10 return |
|---|---:|---:|---:|---:|---:|
| ATR(14) x 3.0 | +0.64% | -5.94% | 34.38% | -5.76% | -1.23% |
| ATR(28) x 3.0 | -0.33% | -7.42% | 35.48% | -1.71% | -1.61% |

The remaining seven training candidates returned between -6.01% and -14.84%. Fees were the main
structural drag: the training winner paid 3,213.09 USDT in fees on 100,000 USDT initial equity,
while net funding was only 18.76 USDT.

## Interpretation

The 15m ATR reversal baseline is too turnover-heavy for BTC at the tested costs and does not show
stable parameter behavior. The training winner reversed rank out of sample, and both retained
candidates lost money in July and August. Raising leverage would scale an unvalidated negative edge
and is therefore excluded.

This result rejects only this ATR baseline and fixed control set. It does not establish that no BTC
strategy can meet the target, but the current evidence provides no basis for claiming 25% monthly
returns.
