# Sample Return Target Validation

Frozen evaluation endpoint: `1785652557073` (`2026-08-02 14:35:57` Asia/Shanghai).

The replay uses persisted Binance trades, pre-range official 15-minute OHLCV only for
warmup, next-Tick fills, configured Taker fees and slippage, and persisted futures funding.

## Production Candidate

| Account | Parameters | Target exposure | Net return | Max drawdown | Completed trades |
|---|---|---:|---:|---:|---:|
| SOXLB spot | ATR(21) x 4.0 | 1.00x | 29.83% | -6.72% | 1 |
| SOXL perpetual | ATR(21) x 4.0 | 1.25x | 23.00% | -9.32% | 2 |

Machine-readable results are in
`reports/validation/atr_tick_grid_20260802T071037Z.json`.

Reproduction command:

```bash
PYTHONPATH=src python -m mastermind_tick.backtest \
  --periods 21 --multipliers 4.0 \
  --end-ms 1785652557073 --minimum-return 0.20 \
  --output-dir reports/validation
```

## Cost Stress

With fees and slippage doubled, while preserving all other execution rules:

| Account | Net return | Max drawdown |
|---|---:|---:|
| SOXLB spot | 29.24% | -7.00% |
| SOXL perpetual | 22.50% | -9.49% |

## Scope

This is an in-sample target over less than three days of stored Tick history. It proves
the requested return only for the frozen interval and does not establish expected future
returns. Continue paper trading and use rolling out-of-sample windows before considering
real capital.
