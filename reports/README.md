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
- `experiments/deep_factor_v2/2026-08-15/`: cross-asset causal Transformer runs, including the
  latest multimodal version with Binance open-interest, taker-flow, and crowding inputs. Neither
  the OHLCV-only nor multimodal model produced a development-risk-eligible component; both remain
  rejected and disconnected from execution.
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
- `experiments/static_factor_portfolio/2026-08-15/`: development-selected fixed three/four-sleeve
  BTC/ETH portfolios from the discovery-frozen 40-factor universe. The selected four-sleeve 4x
  portfolio returned +184.88% with -18.29% daily-close drawdown in reused 2026 confirmation, but
  reached the 25% target in only 3/8 months and remains rejected.
- `experiments/expanded_factor_portfolio/2026-08-15/`: deterministic beam search over all 152
  discovery-eligible static sleeves. It selected the existing four-sleeve baseline, showing that
  the prior 40-factor cap and maximum sleeve count were not the missing-return bottleneck.
- `experiments/continuous_factor/2026-08-15/`: three-seed GPU XGBoost regression factors trained
  on 2021-2022 with 2023 checkpoint calibration and 2024/2025 selection. The selected ETH model
  lost 50.88% in reused 2026 confirmation and is rejected.
- `experiments/walk_forward_factor/2026-08-15/`: annually refreshed GPU XGBoost BTC/ETH factors
  and a joint development-only search with the frozen static event anchor. Jointly searching all
  43 risk-eligible BTC models produced +128.07% with -16.12% drawdown under base costs and stayed
  positive under stress, but still reached only 3/8 target months. It remains research-only.
- `experiments/marginal_factor_portfolio/2026-08-15/`: all 2,988 static factors are tested as
  marginal sleeves without requiring standalone profitability. The development winner collapsed
  by 67.59% alone in 2026 and left the combined portfolio at 3/8 target months.
- `experiments/factor_overlay/2026-08-15/`: causal daily/monthly exposure rules driven by prior
  anchor, BTC, ETH, or relative returns. The selected ETH state rule reached only 1/8 target
  months under base costs and none under stress.
- `experiments/factor_risk_overlay/2026-08-15/`: leverage with next-day monthly loss and profit
  locks. All 20 development-risk-eligible configurations remained at 3/8 target months or less in
  the explicitly non-selective 2026 neighborhood diagnostic.
- `experiments/funding_spread_factor/2026-08-15/`: BTC/ETH realized funding-spread carry and
  crowding-follow pairs. The selected market-neutral pair was approximately flat in 2026 and left
  the static hybrid at 3/8 target months.
- `experiments/funding_event_factor/2026-08-15/`: extreme funding z-score continuation/reversal
  events and two-factor hybrids. The development-selected hybrid returned +217.30% with -19.76%
  drawdown in reused 2026, but still reached only 3/8 target months.
- `experiments/event_meta_factor/2026-08-15/`: GPU XGBoost meta-label filter for sparse BTC-shock
  events. Its ROC AUC fell below 0.50 in both selection and reused confirmation, and no risk/return
  configuration passed development gates, so the ML filter is rejected.
- `experiments/adaptive_factor_portfolio/2026-08-15/`: causal monthly rotation across a
  discovery-frozen 41-sleeve BTC/ETH universe. The development-selected online configuration fell
  from +487.84% in selection to -1.44% and 0/8 target months in reused confirmation, rejecting
  trailing-performance factor rotation.
- `experiments/event_consensus/2026-08-15/`: simultaneous voting across discovery-selected BTC/ETH
  shock-event groups, including follow and crowd-fade modes. The selected portfolio discarded ETH
  and produced 0/8 target months in reused confirmation, so event consensus is rejected.
- `experiments/beta_hedged_factor_book/2026-08-15/`: shared-equity replay of the frozen four-factor
  BTC/ETH book with fixed common-beta hedges selected only on 2021-2025. The selected half-risk,
  25% BTC hedge reduced 2026 drawdown to -12.62% but reached the 25% target in only 1/8 months,
  so beta hedging is rejected as a route to the monthly-return objective.
- `experiments/market_metric_factor/2026-08-15/`: causal Binance futures open-interest, taker-flow,
  and trader-crowding factors added to the frozen four-factor anchor. The development-selected
  ETH crowding-fade hybrid remained profitable under 10+5 bps stress costs but reached the revised
  15% target in only 3/8 reused 2026 months. Requiring 25% target-month coverage in each development
  segment produced no eligible hybrid. It remains rejected and research-only.
- `experiments/market_metric_volatility/2026-08-15/`: causal volatility scaling on the ETH
  crowding hybrid. The selected configuration and all 149 development-eligible neighbors remained
  at 3/8 target months or fewer in reused confirmation, so the overlay is rejected.
- `experiments/bar_factor_hybrid/2026-08-15/`: 168 BTC/ETH EMA, Donchian, momentum, and RSI sleeves
  around the frozen factor book. None of 130 development-eligible hybrids passed confirmation, so
  conventional bar-strategy diversification is rejected for this objective.
- `experiments/market_state_overlay/2026-08-15/`: prior-day BTC top-account crowding controls the
  next day's frozen factor-book exposure. The corrected causal run reached the revised +15% target
  in only 2/8 reused 2026 months under base costs and 1/8 under stress, so it is rejected. Earlier
  reports showing 4/8 used same-day metric snapshots and are invalid due to forward leakage; they
  remain only as provenance. No market-state overlay is approved for trading.
- `experiments/market_state_volatility/2026-08-15/`: a prior-day ETH crowding-spread state and a
  causal 20-day volatility target on the frozen factor book. The development-selected result met
  the revised +15% target in 4/8 reused 2026 months under both base and stress costs, with -28.19%
  and -32.50% daily-close drawdowns. It is frozen as a forward research candidate, not approved
  for trading; 2026 is reused and peak modeled notional is approximately 8.8x.
- `experiments/monthly_target_feasibility/2026-08-15/`: strict audit of the requirement that every
  complete month return at least +15%. An explicitly ex-post formula reaches 7/7 January-July
  months under base and stress costs, but fails development risk gates; the causal volatility-
  controlled version reaches only 3/7. The result is rejected for selection bias and development
  failure, is not approved for trading, and excludes partial August from the goal audit.
- `experiments/monthly_target_regime_router/2026-08-15/`: persistent closed-bar MACD sleeves are
  tested as fixed mixtures and prior-day causal routes around the frozen market-state strategy.
  Of 540 MACD variants, 92 profit in both development splits; 736 fixed mixtures and 828 routed
  configurations pass the development return and drawdown gates. Both families reach at most 4/7
  complete January-July 2026 months under base and stress costs, with no 7/7 configuration. This
  direction is rejected, remains research-only, and excludes partial August from strict counts.
- `experiments/defensive_factor_portfolio/2026-08-15/`: all 2,988 predefined BTC/ETH factors are
  screened only on their returns during frozen-baseline loss months in 2021-2025. Seventy-eight
  sparse event factors pass the conditional screen, but only four factor/weight/leverage/monthly-
  lock configurations pass base and stress development gates. The best reused-confirmation result
  remains 4/7 complete months and loses in May and July, so conditional defensive-factor selection
  is rejected as a solution to the strict every-month +15% target.
- `experiments/fast_trend_complement/2026-08-15/`: 224 predeclared BTC/ETH 4h and daily fast
  time-series momentum sleeves test 1-20 day lookbacks, fixed deadbands, and one/two-bar causal
  confirmation. Sixty candidates are shortlisted on 2021-2025 only and 59 risk configurations
  pass base and stress development gates, but the best reused-confirmation result remains 4/7 and
  still loses in May and July. Fast directional trend is rejected for the strict monthly target.
- `experiments/order_flow_complement/2026-08-15/`: 1,280 predefined BTC 4h aggregate-trade
  order-flow factors are selected only on 2024/2025, then tested alone and in development-ranked
  two-factor portfolios around the frozen market-state strategy. Of 2,982 development-eligible
  risk configurations, none reaches +15% in all seven complete 2026 months under base and stress
  costs; the best strict coverage is 5/7. The study is rejected and excludes partial August.
- `experiments/volatility_order_flow_router/2026-08-15/`: prior-day BTC realized volatility
  routes between the frozen state strategy and 22 development-selected order-flow factors plus
  18 development-valid pairs. The 10,080-route grid and 2,017 development-eligible risk controls
  reach at most 5/7 complete 2026 months under both cost models; May and July remain losses. The
  causal low-volatility routing hypothesis is rejected and partial August is excluded.
- `experiments/monthly_robust_ensemble/2026-08-15/`: 540 predefined MACD sleeves and 40
  development-valid order-flow sleeves are screened and combined by the minimum monthly stability
  across 2024/2025 and both cost models. The beam search produces 280 portfolios and 725 risk-
  eligible controls, but the best reused-confirmation result remains 5/7 and loses in May and
  July. Static monthly-stability optimization is rejected and partial August is excluded.
- `experiments/drawdown_recovery_trend/2026-08-15/`: single and paired MACD recovery sleeves are
  selected only when positive in every 2024/2025 loss month of the frozen monthly-robust baseline,
  then activated one day after a causal monthly drawdown trigger. The search audits 25,772
  development-risk-eligible controls; none exceeds 5/7 complete 2026 months under both cost
  models, and May/July remain negative. The recovery mechanism is rejected.
- `experiments/static_defensive_trend/2026-08-15/`: the same 119 development-selected MACD
  sleeves and 60 development-ranked pairs are held statically beside the frozen monthly-robust
  baseline. Of 762 development-risk-eligible controls, the best reused-confirmation diagnostic
  reaches 6/7 under both cost models: May and July clear +15%, while June remains negative. This
  is the closest valid-protocol result so far, but it is not a strict solution or trading approval.
- `experiments/volatility_guarded_trend/2026-08-15/`: a causal prior-day BTC realized-volatility
  guard reduces the BTC daily MACD trend sleeve from 55% in calm states to 5% in volatile states.
  The predeclared coarse grid has 720 development-risk-eligible controls and 0 strict 7/7 results;
  a confirmation-informed local neighborhood has 134 eligible controls and 5 reused-confirmation
  7/7 results. The representative 7/7 is explicitly marked post-confirmation refinement, not
  approved for trading, and partial August is excluded.
- `experiments/walkforward_volatility_guard/2026-08-15/`: a formal 2021-2023 training and
  2024-2025 validation search freezes daily BTC/ETH MACD, prior-day volatility routing, leverage,
  and monthly locks before auditing reused 2026 confirmation. The 120-route shortlist produces
  278 development-risk-eligible controls, but none reaches +15% in all seven complete months under
  both cost models. The development-selected result reaches only 4/7 and misses April, May, and
  July, so the walk-forward hypothesis is rejected and partial August is excluded.
- `experiments/calendar_month_router/2026-08-15/`: fixed month-of-year BTC/ETH daily MACD maps are
  learned on 2021-2023, while routing and risk controls are selected on 2024-2025. The selected
  long/short calendar reaches 5/7 complete 2026 months under both cost models but loses in May and
  July; none of 1,630 development-risk-eligible controls reaches strict 7/7.
- `experiments/expanding_calendar_router/2026-08-15/`: calendar mappings are rebuilt causally for
  each 2023-2025 validation year, then refit on all 2021-2025 data before 2026 confirmation. The
  development-selected long-only top-three calendar reaches 6/7 under base and stress costs and
  fixes July, but loses 25.45%/20.94% in May. No one of 3,819 eligible controls reaches 7/7.
- `experiments/momentum_calendar_router/2026-08-15/`: prior-day BTC momentum switches between
  expanding long-only and long/short month maps. The development selection remains 6/7 and loses
  in May under both cost models; all 4,731 eligible controls fail strict 7/7, rejecting daily
  momentum as the missing downside source.
- `experiments/multiscale_calendar_router/2026-08-15/` and
  `experiments/fast_multiscale_calendar_router/2026-08-15/`: confirmation-informed family audits
  add 1h/4h candidates to the expanding calendar or isolate them from daily candidates. Neither
  family produces a strict configuration; mixed intervals reach at most 6/7 diagnostically and
  fast-only intervals reach at most 4/7 under both costs.
- `experiments/drawdown_calendar_router/2026-08-15/`: a causal month-to-date loss trigger switches
  persistently from long-only to long/short calendar maps on the following day. The selected rule
  remains 6/7 and deepens the May loss, while none of 2,985 development-risk-eligible controls
  reaches strict 7/7. This post-confirmation mechanism extension is rejected.
- `experiments/conditional_calendar_complement/2026-08-15/`: prior-year weak-month screens add
  development-ranked daily MACD complements to the frozen expanding calendar. The long/short and
  derived short-only searches both remain 6/7 and lose heavily in May; neither search contains a
  strict base-and-stress configuration. This post-confirmation family extension is rejected.
- `experiments/provider_calendar_router/2026-08-15/`: each calendar month chooses between the
  frozen expanding calendar and a coarse volatility-guard provider using only earlier same-month
  results. The development-selected route improves January and February but retains the calendar
  provider in May, so all 156 eligible controls remain at or below 6/7. The family is rejected.

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
