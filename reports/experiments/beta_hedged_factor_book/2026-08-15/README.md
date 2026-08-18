# Beta-Hedged Factor Book

This experiment replays the frozen four-factor BTC/ETH portfolio in one shared mark-to-market
equity account. Targets come from closed 4h bars and fill at the next 4h open. The replay charges
incremental turnover costs and historical futures funding.

The search uses only 2021-2025 data to choose the hedge asset, fixed beta, hedge ratio, and risk
scale. The 2026 interval is reused confirmation evidence and does not participate in selection.

## Result

The selected configuration uses BTC as the hedge with beta `1.0`, a `25%` hedge ratio, and a
`0.5x` risk scale. In 2026 it returned `+77.67%` with `-12.62%` maximum daily-close drawdown under
base costs, but reached `+25%` in only `1/8` months. Under `10 bps` fee plus `5 bps` slippage it
returned `+61.05%` and reached the monthly target in `0/8` months.

Decision: `rejected_after_confirmation`. Approved for trading: `false`.

## Reproduce

```bash
.venv/bin/python scripts/research/mine_beta_hedged_factor_book.py
```

The timestamped JSON artifact contains the complete daily/monthly metrics and selection metadata;
the matching Markdown file is the human-readable summary.
