# Experiment Catalog

Experiments are grouped by research question below. Each experiment keeps immutable dated evidence
inside its own directory. The authoritative strategy status remains in
[`../../strategies/`](../../strategies/README.md).

## Active Forward Observation

| Experiment | Question | Current status |
|---|---|---|
| [`factor_stability/`](factor_stability/) | Does a single causal factor retain IC across years and regimes? | Frozen 4h reversal factors collecting new data; Transformer combination blocked. |
| [`btc_strategy_families/`](btc_strategy_families/) | Does the frozen BTC daily EMA survive forward data? | Observation only; not approved. |
| [`soxl_volatility_spread/`](soxl_volatility_spread/) | Does the frozen SOXL true-range spread survive forward data? | Observation only; not approved. |

## Market And Strategy Baselines

- BTC: [`btc_atr/`](btc_atr/), [`btc_non_atr_stability/`](btc_non_atr_stability/),
  [`btc_regime_breakout/`](btc_regime_breakout/), and
  [`btc_strategy_families/`](btc_strategy_families/).
- SOXL: [`soxl_atr/`](soxl_atr/) and [`soxl_volatility_spread/`](soxl_volatility_spread/).
- Cross-asset and relative value: [`btc_eth_pair/`](btc_eth_pair/),
  [`short_horizon_relative_value/`](short_horizon_relative_value/), and
  [`btc_high_frequency/`](btc_high_frequency/).

## Factor Discovery And ML

- Formula and cross-asset factors: [`factor_mining/`](factor_mining/),
  [`cross_asset_factor/`](cross_asset_factor/), [`continuous_factor/`](continuous_factor/), and
  [`walk_forward_factor/`](walk_forward_factor/).
- Deep learning: [`deep_factor/`](deep_factor/) and [`deep_factor_v2/`](deep_factor_v2/).
- Event, flow, and market metrics: [`btc_order_flow/`](btc_order_flow/),
  [`event_meta_factor/`](event_meta_factor/), [`funding_event_factor/`](funding_event_factor/),
  [`funding_spread_factor/`](funding_spread_factor/), and
  [`market_metric_factor/`](market_metric_factor/).
- Single-factor validation: [`factor_stability/`](factor_stability/).

## Portfolio And Overlay Research

- Factor books: [`factor_portfolio/`](factor_portfolio/),
  [`static_factor_portfolio/`](static_factor_portfolio/),
  [`expanded_factor_portfolio/`](expanded_factor_portfolio/),
  [`adaptive_factor_portfolio/`](adaptive_factor_portfolio/),
  [`defensive_factor_portfolio/`](defensive_factor_portfolio/),
  [`marginal_factor_portfolio/`](marginal_factor_portfolio/), and
  [`beta_hedged_factor_book/`](beta_hedged_factor_book/).
- Risk and state overlays: [`factor_overlay/`](factor_overlay/),
  [`factor_risk_overlay/`](factor_risk_overlay/),
  [`market_metric_volatility/`](market_metric_volatility/),
  [`market_state_overlay/`](market_state_overlay/), and
  [`market_state_volatility/`](market_state_volatility/).
- Event combinations: [`btc_eth_lead_lag/`](btc_eth_lead_lag/),
  [`event_consensus/`](event_consensus/), and [`bar_factor_hybrid/`](bar_factor_hybrid/).

## Calendar, Trend, And Risk Routing

- Calendar families: [`calendar_month_router/`](calendar_month_router/),
  [`expanding_calendar_router/`](expanding_calendar_router/),
  [`momentum_calendar_router/`](momentum_calendar_router/),
  [`provider_calendar_router/`](provider_calendar_router/),
  [`conditional_calendar_complement/`](conditional_calendar_complement/), and
  [`drawdown_calendar_router/`](drawdown_calendar_router/).
- Trend families: [`monthly_robust_ensemble/`](monthly_robust_ensemble/),
  [`static_defensive_trend/`](static_defensive_trend/),
  [`fast_trend_complement/`](fast_trend_complement/),
  [`drawdown_recovery_trend/`](drawdown_recovery_trend/), and
  [`volatility_guarded_trend/`](volatility_guarded_trend/).
- Walk-forward and flow routing: [`walkforward_volatility_guard/`](walkforward_volatility_guard/),
  [`order_flow_complement/`](order_flow_complement/), and
  [`volatility_order_flow_router/`](volatility_order_flow_router/).
- Target feasibility: [`monthly_target_feasibility/`](monthly_target_feasibility/) and
  [`monthly_target_regime_router/`](monthly_target_regime_router/).

## Post-Confirmation Diagnostics

The following directories are retained as negative or diagnostic evidence and must not be promoted
without a new protocol: [`fast_multiscale_calendar_router/`](fast_multiscale_calendar_router/),
[`multiscale_calendar_router/`](multiscale_calendar_router/),
[`high_annualized_arbitrage_candidates/`](high_annualized_arbitrage_candidates/), and the rejected
neighbors recorded alongside each dated experiment.

Superseded implementation iterations belong under [`../archive/`](../archive/), not beside the
authoritative dated result.
