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
- `experiments/*/forward/`: deterministic monitoring output for frozen parameters. Forward reports
  must not run a search or count UTC dates on or before the candidate evidence lock.
- `experiments/soxl_volatility_spread/2026-08-14-multihorizon/`: exploratory 15m/30m/60m
  volatility-spread ensemble; it has no clean post-reveal holdout and is not a candidate approval.
- `experiments/soxl_volatility_spread/2026-08-14-state-filter/`: exploratory 15m-state entry
  filters layered on the higher-horizon sleeves; no filter combination is approved.
- `experiments/soxl_volatility_spread/2026-08-14-compression-fade/`: exploratory compressed-range
  mean-reversion sleeve. Its train/validation portfolio selection assigned it zero weight, so it
  is rejected as an additive return source and is not a candidate approval.
- `experiments/soxl_volatility_spread/2026-08-14-risk-budget/`: closed-bar spread-strength entry
  sizing. It reduced diagnostic drawdown only by reducing return, so it is rejected as a route to
  the 5% daily target and is not a candidate approval.
- `experiments/soxl_volatility_spread/2026-08-14-cross-asset/`: BTC 15m volatility-state filters
  for the frozen SOXL signal. The best split result had lower development return and worse Tick
  drawdown than the fixed baseline, so it is rejected and is not a candidate approval.
- `experiments/soxl_volatility_spread/2026-08-14-5m/`: 5m volatility-spread replay constructed
  from persisted 250ms trades. No candidate passed train/validation, so this time scale is rejected
  before forward monitoring.
- `experiments/soxl_volatility_spread/2026-08-14-acceleration/`: 15m spread-acceleration and
  first-cross entry gates. Stable selection chose no gate, so the hypothesis is rejected rather
  than promoted as a new strategy.
- `experiments/soxl_volatility_spread/2026-08-14-protocol.md`: discovery stop rule and forward
  evidence protocol. It freezes parameter searching through the current evidence lock to prevent
  data-snooping against the revealed diagnostic interval.
- `experiments/btc_atr/2026-08-14-baseline/`: BTCUSDT 15m ATR baseline study after expanding the
  warehouse to 2024-01-01. The training winner failed both July validation and August confirmation,
  so no BTC strategy or leverage setting is approved.
- `experiments/btc_atr/2026-08-14-stability/`: 216 BTCUSDT candidates across ATR trailing stops,
  Keltner breakouts, ATR-normalized mean reversion, and Chandelier exits on 1h, 4h, and daily bars.
  No development-selected family winner or ex-post candidate passed the multi-split stability
  gates, so no ATR strategy is approved.
- `experiments/btc_strategy_families/2026-08-14/`: 1h, 4h, and daily BTCUSDT comparison of EMA
  trend, Donchian breakout, time-series momentum, and RSI mean reversion. No strategy meets the
  original 25% monthly objective. Under the revised single-month 15% gate, the frozen EMA lead is a
  provisional forward candidate, still unapproved for trading.
- `experiments/btc_eth_pair/2026-08-14/`: OHLCV-level equal-notional BTC/ETH ratio EMA, momentum,
  and mean-reversion research. The selected pair was positive but underperformed the benchmark and
  missed the monthly target; it is rejected and not a Tick-level approval.
- `experiments/btc_regime_breakout/2026-08-14/`: daily EMA direction filtered 4h BTC Donchian
  breakouts. The development winner went flat during confirmation, while the faster long/short
  variant failed confirmation; no regime-filtered candidate is approved.
- `experiments/factor_mining/2026-08-15/`: AlphaGPT-inspired, causal factor-expression search
  across BTCUSDT, ETHUSDT, and SOXLUSDT. BTC and ETH candidates failed independent confirmation;
  SOXL has inadequate independent history. No formula is approved or connected to execution.
- `experiments/deep_factor/`: GPU-only causal Transformer studies. Checkpoints are stored outside
  version control; reports retain the split metrics, costs, configuration, and rejection decision.
- `experiments/btc_order_flow/`: causal BTC aggregate-trade order-flow searches with reported and
  tick-rule direction sources evaluated independently.
- `experiments/cross_asset_factor/`: causal BTC/ETH common-regime, rotation, relative-value, and
  adaptive factor-efficacy portfolios with development-only exposure selection.
- `experiments/btc_eth_lead_lag/2026-08-15/`: causal 4h BTC return-shock factors trading delayed
  ETH response. This is the strongest factor found so far, but its development-selected dynamic
  sizing reached the 25% monthly target in only 2/8 reused confirmation months, below the required
  4/8. It remains rejected and research-only; 2026 is not a fresh holdout.
- `experiments/factor_portfolio/2026-08-15/`: 2,988-candidate BTC/ETH second-sleeve search around
  the lead-lag factor. The development-selected fixed-capital portfolio retained only 2/8 target
  months in reused confirmation, so static factor diversification is rejected.
- `experiments/event_meta_factor/2026-08-15/`: GPU XGBoost meta-label filter for sparse BTC-shock
  events. Its ROC AUC fell below 0.50 in both selection and reused confirmation, and no risk/return
  configuration passed development gates, so the ML filter is rejected.
- `experiments/adaptive_factor_portfolio/2026-08-15/`: causal monthly rotation across a
  discovery-frozen 41-sleeve BTC/ETH universe. The development-selected online configuration fell
  from +487.84% in selection to -1.44% and 0/8 target months in reused confirmation, rejecting
  trailing-performance factor rotation.

The strategy catalog lives in [`../strategies/`](../strategies/README.md). When a report and the
catalog disagree, the catalog is authoritative for the current research baseline and deployment
snapshots.

## Report Policy

Every promoted study should state its UTC data range, warmup behavior, fee and slippage model,
funding treatment, leverage, position fraction, and whether its holdout data participated in
selection. JSON files are machine-readable evidence; Markdown files summarize decisions and
limitations.

Reports must not be described as production-ready solely because they maximize in-sample return.
Candidates remain unapproved until they are stable across time splits and parameter neighborhoods.
