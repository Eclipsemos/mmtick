# BTC SMA11/40 Active 1.60X Forward-Freeze Candidate

This is a research freeze for shadow/forward observation, not approval for live trading.

## Frozen specification

- Symbol: `BTCUSDT`
- Signal: completed UTC daily candles; next 15m open execution
- Fast/slow SMA: `11/40`
- Bear rule: close below SMA40 and SMA11 below SMA40 for 2 consecutive daily bars
- Recovery rule: 1 consecutive non-bearish daily bar
- Bear target: `0X`; active target: `1.60X`
- Wallets: 50% spot and 50% isolated USD-M collateral
- Futures opening control: `2.5X`; observed effective leverage hard limit `3X`
- Primary costs: 10 bps fee + 5 bps slippage per side, historical Funding

## Observation discipline

This candidate was selected from a predeclared 54-point neighborhood after historical data had
already been inspected. It is not an untouched OOS result. From a new data endpoint onward, record
all 15m target changes, fees, Funding, effective leverage, and BTC spot B&H comparison without
changing parameters or execution assumptions. Any retuning starts a new experiment.

## Reproducibility

- Audit script SHA-256: `21dcbbb5e197ec72e1bac66e5a62a167e035d988c736e737d9bfcbb3b0300dc8`
- Audit data endpoint: `2026-09-03T01:14:59.999Z`
- Historical audit: [README.md](README.md)
- Neighborhood audit: [README.md](../../btc_active155_neighborhood/2026-09-03/README.md)
- Status: `RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE`
