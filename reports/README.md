# Research Reports

This directory contains reproducible research artifacts for the research-only branch. It is not
an operational log and must not contain account databases, credentials, live orders, or generated
market data.

## Layout

- `soxl_perp_full_history/`: current listing-to-date SOXLUSDT research, including summaries,
  optimization evidence, monthly replays, reassessments, and standard replay output.
- `archive/pre_full_history/`: superseded short-sample studies created before listing-to-date data
  was available. These files are retained for provenance and are not current recommendations.
- `experiments/`: optional destination for new exploratory output. Promote an experiment into the
  full-history tree only after documenting its data range, costs, split method, and limitations.

The frozen strategy definition lives in [`../STRATEGY.md`](../STRATEGY.md). When a report and that
document disagree, `STRATEGY.md` is authoritative for the current research baseline.

## Report Policy

Every promoted study should state its UTC data range, warmup behavior, fee and slippage model,
funding treatment, leverage, position fraction, and whether its holdout data participated in
selection. JSON files are machine-readable evidence; Markdown files summarize decisions and
limitations.

Reports must not be described as production-ready solely because they maximize in-sample return.
Candidates remain unapproved until they are stable across time splits and parameter neighborhoods.
