# BTC Active 1.55X Forward-Freeze Candidate

This file records the exact candidate that may be observed forward. It is not a live-trading approval.

## Frozen parameters

- Symbol: `BTCUSDT`
- Signal bars: completed UTC daily bars
- Fast/slow SMA: `10/40`
- Bear confirmation: 3 consecutive bearish daily bars
- Recovery confirmation: 1 consecutive non-bearish daily bar
- Bear target exposure: `0X`
- Active target exposure: `1.55X`
- Wallet structure: 50% spot, 50% isolated USD-M collateral
- Futures opening control: `2.5X`; observed effective leverage must remain `<=3X`
- Costs for primary ledger: 10 bps fee + 5 bps slippage per side, historical Funding
- Execution: target change at the next 15m open after the completed daily signal

## Freeze protocol

The candidate was identified after historical exploration and therefore is not an untouched
out-of-sample selection. Starting from a new data endpoint, append observations without changing
parameters, costs, execution, or collateral assumptions. Record every target change, effective
leverage, funding charge, and comparison with BTC spot B&H. Re-running the historical grid or
changing parameters invalidates this freeze.

## Reproducibility

- Audit script SHA-256: `8aa3afd5a119361665dc975431c01241bcc3be0eb983fd65e80fb6e5c6796f41`
- Data endpoint used in audit: `2026-09-03T01:14:59.999Z`
- Historical audit: [README.md](README.md)
- Status: `RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE`
