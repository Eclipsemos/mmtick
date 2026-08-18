# SOXLUSDT Full-History Research

This tree contains research based on Binance SOXLUSDT listing-to-date data. The current frozen
baseline is long-only 15-minute `ATR(32) x 3`, trend efficiency `8 / 0.25`, fixed 15-minute action
lock, no profit protection, no continuation re-entry, and 1.25x target exposure. See
[`../../strategies/current_research_baseline.md`](../../strategies/current_research_baseline.md)
for the complete definition.

## Current Decision Trail

- [`summaries/strategy_fit_reassessment_20260808.md`](summaries/strategy_fit_reassessment_20260808.md):
  latest fit, monthly performance, concentration, cost, and control reassessment.
- [`summaries/final_optimization_20260807.md`](summaries/final_optimization_20260807.md): walk-forward
  selection of the frozen baseline.
- [`summaries/multitimeframe_optimization_20260807.md`](summaries/multitimeframe_optimization_20260807.md):
  multi-timeframe robustness check and rejected higher-return candidates.
- [`summaries/strategy_regime_analysis_20260807.json`](summaries/strategy_regime_analysis_20260807.json):
  machine-readable regime diagnostics.

The files named `current_strategy_20260807T131512Z` and `no_reentry_20260807T132329Z` describe the
rejected ATR(21) x 4 predecessor. They remain in `summaries/` as historical comparison evidence and
are not the current baseline.

## Layout

- `summaries/`: conclusions, decision documents, regime analysis, and compact comparison replays.
- `optimization/`: raw stage 1-23 search output and direction baseline evidence.
- `monthly/`: independent calendar-month replay artifacts.
- `reassessment/`: dated frozen-parameter reassessments using newly appended data.
- `replay/standard/`: canonical standard replay output for the selected configuration.

Large JSON files are retained because they provide the parameter-grid evidence behind the summary
documents. New generated output should use a dated experiment directory until it is reviewed.
