# SOXL 20x Margin and Fixed Profit-Taker Evaluation

## Decision

Do not deploy this proposal as a strategy whose loss is "capped at 2.5%." It is a
promising research variant at small allocation, but the current replay does not model
liquidation. The 2.5% / 20x / 1.5 ATR configuration had two historical closed trades whose
loss exceeded their implied entry margin, including one loss of 3.16% of initial account
equity. Those trades would likely have followed a different liquidation path in production.

## Setup

- Market: Binance `SOXLUSDT` perpetual, long-only.
- Data: 250 ms aggregate ticks, 2026-05-17 16:00 UTC through 2026-08-26 09:32 UTC.
- Signal: live-equivalent startup, 15-minute ATR(32) x 3, efficiency 8 / 0.25.
- Costs: 5 bps fee and 2 bps slippage per side, with recorded funding.
- Sizing: 20x leverage and 1%, 2.5%, 5%, 7.5%, or 10% of current equity as margin.
- Fixed targets: none, 0.5, 1, 1.5, 2, 3, or 4 entry ATR.
- Initial equity: 100,000 USDT. No liquidation, maintenance margin, risk-tier, or
  liquidation-fee model is present.

At 2.5% margin and 20x, target notional is 50% of account equity. A 1% adverse price move
therefore loses about 0.5% of account equity before fees and funding.

## Results at 2.5% Margin

Each cell is net return / maximum drawdown. Period replays start with fresh 100,000 USDT.

| Fixed target | May 17-Aug 26 | May 17-Jun 30 | July 1-30 | Jul 31-Aug 26 | Aug 9-26 |
|---|---:|---:|---:|---:|---:|
| None | +53.26% / -11.64% | +30.78% / -8.91% | +12.11% / -11.64% | +5.76% / -6.17% | -2.01% / -5.78% |
| 0.5 ATR | +0.18% / -10.37% | -4.18% / -10.37% | +6.26% / -6.09% | -1.61% / -4.40% | -0.13% / -3.64% |
| 1.0 ATR | +12.42% / -9.07% | +2.17% / -7.13% | +7.44% / -7.29% | +2.42% / -4.38% | +2.66% / -2.84% |
| 1.5 ATR | +16.87% / -14.46% | -0.50% / -9.96% | +12.87% / -7.39% | +4.06% / -4.42% | +4.49% / -2.16% |
| 2.0 ATR | +18.84% / -11.27% | +12.79% / -7.45% | +2.96% / -10.46% | +2.33% / -4.39% | +1.14% / -3.16% |
| 3.0 ATR | +27.71% / -10.43% | +16.48% / -6.98% | +4.98% / -10.29% | +4.44% / -4.42% | -0.45% / -3.82% |
| 4.0 ATR | +31.10% / -11.20% | +20.28% / -6.79% | +6.11% / -11.20% | +2.72% / -5.97% | -2.11% / -5.31% |

The 1.5 ATR target is strongest only in the recent window. It is negative in training, so it
is not a stable winner. Larger 3-4 ATR targets perform better in the early sample but fail in
the recent window. The target is therefore regime-sensitive.

For 1.5 ATR, increasing margin from 2.5% to 5%, 7.5%, and 10% raised full-period return to
33.84%, 50.17%, and 65.06%, but maximum drawdown rose to 27.28%, 38.58%, and 48.47%.
This is risk scaling, not additional alpha.

## Leverage Ceiling and Exchange Constraints

Public Binance `exchangeInfo` currently reports for `SOXLUSDT`:

- `requiredMarginPercent`: 5.0% (consistent with a 20x initial-margin ceiling)
- `maintMarginPercent`: 2.5%
- `liquidationFee`: 1.5%

The account-authenticated leverage-bracket endpoint remains the final authority, but 20x
should be treated as the maximum usable leverage unless the account UI/API explicitly shows
otherwise. At fixed 2.5% margin, the unconstrained 1.5 ATR replay's 14.46% drawdown would
linearly imply about 41.5x before reaching a 30% drawdown. That extrapolation is irrelevant
once the symbol's 20x ceiling and liquidation mechanics are applied.

Ignoring fees and slippage, a long at 20x with 2.5% maintenance margin reaches liquidation
after roughly a 2.5% adverse price move (`1 / 20 - 0.025`). With 2.5% account margin, that
corresponds to about 1.25% account loss before liquidation costs, not a guaranteed 2.5% cap.
Gaps and liquidation execution can increase it. A 30% account drawdown limit therefore does
not justify increasing leverage beyond 20x; it only leaves substantial portfolio-level room
if the isolated position is correctly contained.

## Risk Finding

The 2.5% margin is not a hard account-loss cap. The replay's worst closed trade at the 1.5
ATR target lost 3,158.01 USDT, or 3.16% of initial equity and 124.6% of implied entry margin.
Two full-history losses exceeded entry margin. Even the ATR-cross exit is application-side;
gaps, delayed ticks, rejected orders, service failure, liquidation fees, and auto-add margin
can change the realized loss.

## Liquidation-Aware Stress Pass

An additional research-only replay used 20x, 2.5% margin, a 0.5% maintenance-margin
threshold, and a 0.5% liquidation fee. These are explicit approximations, not Binance risk
tiers. It triggered four full-history liquidations. Returns were:

| Target | Unconstrained replay | Approximate liquidation replay |
|---|---:|---:|
| None | +53.26% | +40.22% |
| 1.0 ATR | +12.42% | +5.36% |
| 1.5 ATR | +16.87% | +8.52% |
| 2.0 ATR | +18.84% | +9.29% |
| 3.0 ATR | +27.71% | +15.70% |

No liquidation occurred in the Aug 9-26 window, so its 1.5 ATR result remains +4.49%.
The stress pass demonstrates that liquidation assumptions materially affect historical
performance and must be part of the acceptance test.

Before paper deployment, add a liquidation-aware replay using Binance maintenance-margin
tiers and an exchange-hosted stop. Keep auto-add margin disabled. Research 1% and 2.5%
allocations; reject 5-10% until the liquidation model and longer forward sample pass. Treat
1.0-1.5 ATR as forward candidates, not selected production parameters.

## Reproduction

```bash
PYTHONPATH=src python3 scripts/research/optimize_soxl_strategy.py \
  --grid leveraged-profit-taker \
  --splits full,train,validation,holdout,august9_to_now \
  --live-startup --workers 8 \
  --output reports/experiments/soxl_atr/leveraged-profit-taker-2026-08-26.json
```
