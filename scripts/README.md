# Script Layout

This branch is research-only. Scripts are grouped by responsibility so data maintenance,
experimentation, and report generation are not mixed in one directory.

## Stable Entrypoints

| Area | Purpose | Examples |
|---|---|---|
| `maintenance/` | Idempotent historical-data and metric updates | `import_history.py`, `update_futures_metrics.py` |
| `research/` | Backtests, audits, factor mining, model training, and frozen forward monitors | `research_factor_stability.py`, `monitor_factor_stability_forward.py` |
| `reporting/` | Deterministic rendering from existing research data | `report_soxl_august_atr_trades.py` |
| `run.sh` | API and dashboard launcher | `./scripts/run.sh` |

Common commands:

```bash
# Update bars and funding only; omit --bars-only only when aggregate trades are required.
.venv/bin/python scripts/maintenance/import_history.py --bars-only --help

# Refresh Binance futures market metrics.
.venv/bin/python scripts/maintenance/update_futures_metrics.py --help

# Run the strict single-factor stability study in the GPU/NumPy environment.
/home/spaceaic/env/.venv/bin/python scripts/research/research_factor_stability.py

# Re-evaluate only post-lock observations with immutable parameters.
/home/spaceaic/env/.venv/bin/python scripts/research/monitor_factor_stability_forward.py
```

## Research Naming

- `explore_*`: bounded strategy-family exploration; never an approval by itself.
- `mine_*`: deterministic candidate-grid or factor-universe search.
- `train_*`: optional ML/GPU training isolated from the API process.
- `audit_*`: robustness, feasibility, or previously reported-result audits.
- `evaluate_*` and `monitor_*`: frozen candidate evaluation without parameter search.
- `analyze_*`, `optimize_*`, and `reassess_*`: focused historical diagnostics.

Research scripts may import sibling scripts. Keep those imports inside `scripts/research/`; reusable
domain logic belongs in `src/mastermind_tick/` and must have tests under `tests/`.
