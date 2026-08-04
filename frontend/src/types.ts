export type StrategyView = {
  ready: boolean
  atr: string | null
  trailing_stop: string | null
  price: string | null
  relation: 'above' | 'below' | 'warming'
  bar_start_ms: number | null
  bought_this_bar: boolean
  flattened_this_bar: boolean
  action_this_bar: boolean
  trend_efficiency: string | null
  trend_filter_passed: boolean
  reversal_direction: 'LONG' | 'SHORT' | null
  reversal_anchor: string | null
  reversal_eligible_bar_ms: number | null
  last_cross: 'UP' | 'DOWN' | null
  last_cross_at_ms: number | null
  last_cross_result: 'BUY_SIGNAL' | 'SELL_SIGNAL' | 'BLOCKED' | null
  last_cross_reason: string | null
}

export type DecisionView = {
  state: 'PAUSED' | 'WARMING_UP' | 'ORDER_PENDING' | 'HOLDING_LONG' | 'HOLDING_SHORT' | 'ACTION_LOCKED' | 'REVERSAL_CONFIRMATION' | 'TREND_FILTERED' | 'ARMED_FOR_BUY' | 'ARMED_FOR_LONG' | 'ARMED_FOR_SHORT' | 'WAITING_FOR_RESET'
  reason: string
  next_trigger: string
  trading_enabled: boolean
  has_position: boolean
  position_side: 'LONG' | 'SHORT' | 'FLAT'
  allow_short: boolean
  has_pending_order: boolean
  strategy_ready: boolean
  buy_lock_open: boolean
  reentry_lock_open: boolean
  action_lock_open: boolean
  trend_filter_passed: boolean
  reversal_direction: 'LONG' | 'SHORT' | null
  reversal_eligible_bar_ms: number | null
  fresh_up_cross: boolean
  bar_end_ms: number | null
  signal_confirmation: 'TICK'
  fill_timing: 'NEXT_TICK'
  last_signal: {
    side: 'BUY' | 'SELL'
    status: string
    timestamp_ms: number
    reason: string
  } | null
}

export type Runtime = {
  id: string
  symbol: string
  display_symbol: string
  name: string
  venue: string
  asset_type: string
  reference_symbol: string
  paper_model: 'spot' | 'futures'
  market_data_id: string
  allow_short: boolean
  leverage: number
  margin_mode: string
  position_fraction: number
  target_exposure: number
  fee_bps: number
  slippage_bps: number
  strategy_config: {
    algorithm_version: string
    bar_minutes: number
    atr_period: number
    atr_multiplier: number
    trend_efficiency_period: number
    minimum_trend_efficiency: number
    reversal_confirmation_atr: number
    one_action_per_bar: boolean
    startup_alignment: boolean
    futures_reversal_mode: 'close_then_confirm'
    signal_confirmation: 'tick'
    fill_timing: 'next_tick' | 'binance_actual'
  }
  feed: string
  market_state: {
    mark_price?: string | null
    index_price?: string | null
    funding_rate?: string | null
    next_funding_time_ms?: number | null
    updated_at_ms?: number | null
  }
  kline_state: {
    source: string
    validation: string
    last_official_bar_start_ms: number | null
    last_verified_at_ms: number | null
    mismatches: number
  }
  status: string
  status_message: string
  reconnects: number
  last_tick: { timestamp_ms: number; price: string; quantity: string; source: string } | null
  strategy: StrategyView
  decision: DecisionView
}

export type Account = {
  id: string
  symbol: string
  display_symbol: string
  venue: string
  currency: string
  initial_cash: string
  cash: string
  quantity: string
  average_price: string
  realized_pnl: string
  total_fees: string
  total_funding: string
  equity: string
  total_pnl: string
  total_return: number
  max_drawdown: number
  sharpe_ratio: number | null
  win_rate: number | null
  winning_trades: number
  losing_trades: number
  last_price: string | null
  last_snapshot_ms: number | null
  unrealized_pnl: string
  market_value: string
  mark_price: string | null
  index_price: string | null
  funding_rate: string | null
  initial_margin: string
  available_balance: string
  funding_count: number
  fill_count: number
  round_trips: number
  runtime: Runtime
}

export type Overview = {
  service: string
  environment: string
  trading_enabled: boolean
  started_at_ms: number | null
  instruments: Runtime[]
  accounts: Account[]
  strategy_config: {
    name: string
    bar_minutes: number
    atr_period: number
    atr_multiplier: number
    trend_efficiency_period: number
    minimum_trend_efficiency: number
    reversal_confirmation_atr: number
    one_action_per_bar: boolean
    startup_alignment: boolean
    futures_reversal_mode: 'close_then_confirm'
    signal_confirmation: 'tick'
    fill_timing: 'next_tick' | 'binance_actual'
    position_fraction: number
    fee_bps: number
    slippage_bps: number
  }
}

export type LiveReadiness = {
  account_id: string
  symbol: string
  enabled: boolean
  status: 'STARTING' | 'DISABLED' | 'BLOCKED' | 'OBSERVE_ONLY' | 'ARMED' | 'STOPPED'
  status_message: string
  public_capability: boolean
  credentials_present: boolean
  credential_file_secure: boolean
  signed_account_verified: boolean
  api_reading_enabled: boolean
  spot_trading_permitted: boolean
  withdrawals_enabled: boolean
  ip_restricted: boolean
  allow_order_submission: boolean
  activation_confirmed: boolean
  order_submission_ready: boolean
  persisted_paused: boolean
  reconciliation_ok: boolean
  last_reconciled_at_ms: number | null
  last_trade_sync_at_ms: number | null
  synced_trade_count: number
  block_reasons: string[]
  database: string
  risk_limits: {
    position_fraction: number
    max_order_notional: number
    quote_reserve: number
    max_slippage_bps: number
    max_daily_loss: number
    max_orders_per_day: number
  }
}

export type LiveSession = {
  authenticated: boolean
  configured: boolean
  local_unlock_available: boolean
}

export type EquityPoint = {
  timestamp_ms: number
  price: string
  equity: string
  cash: string
  quantity: string
  unrealized_pnl: string
  mark_price: string | null
  index_price: string | null
  funding_rate: string | null
  initial_margin: string
  available_balance: string
  total_funding: string
  atr: string | null
  trailing_stop: string | null
  relation: string | null
}

export type ReturnPeriod = {
  key: string
  label: string
  start_ms: number
  end_ms: number
  equity: string | null
  return: number | null
}

export type ReturnSummary = {
  account_id: string
  generated_at_ms: number
  as_of_ms: number
  timezone_offset_minutes: number
  initial_equity: string
  current_equity: string
  total_return: number
  annualized_return: number | null
  elapsed_days: number
  return_30d: number | null
  current_week_return: number | null
  current_month_return: number | null
  daily: ReturnPeriod[]
  weekly: ReturnPeriod[]
  monthly: ReturnPeriod[]
}

export type Fill = {
  id: string
  account_id: string
  side: string
  timestamp_ms: number
  price: string
  quantity: string
  notional: string
  fee: string
  reason: string
  source: string
  position_effect: 'OPEN' | 'CLOSE' | null
  position_before: string | null
  position_after: string | null
  realized_pnl: string | null
}

export type FundingPayment = {
  id: string
  account_id: string
  symbol: string
  timestamp_ms: number
  rate: string
  mark_price: string
  quantity: string
  notional: string
  amount: string
  source: string
}

export type Order = {
  id: string
  account_id: string
  side: string
  status: string
  reason: string
  signal_price: string
  atr: string | null
  trailing_stop: string | null
  submitted_at_ms: number
  filled_at_ms: number | null
  fill_price: string | null
}

export type EventItem = {
  id: number
  account_id: string
  timestamp_ms: number
  level: string
  event_type: string
  message: string
}

export type WarehouseTable = {
  name: string
  row_count: number
  size_bytes: number
  average_row_bytes: number
}

export type WarehouseSummary = {
  generated_at_ms: number
  database: {
    path: string
    main_bytes: number
    wal_bytes: number
    shm_bytes: number
    total_bytes: number
    page_size: number
  }
  tables: WarehouseTable[]
  instruments: Array<{
    instrument_id: string
    market_data_id: string
    symbol: string
    agg_trades: {
      row_count: number
      first_timestamp_ms: number | null
      last_timestamp_ms: number | null
      total_quantity: number
      total_notional: number
      raw_trade_count: number
    }
    ohlcv: {
      row_count: number
      closed_count: number
      open_count: number
      first_start_ms: number | null
      last_start_ms: number | null
      last_updated_at_ms: number | null
      interval_minutes: number
    }
  }>
}

export type AggTrade = {
  event_id: string
  instrument_id: string
  symbol: string
  aggregate_trade_id: number | null
  first_trade_id: number | null
  last_trade_id: number | null
  event_time_ms: number | null
  timestamp_ms: number
  price: string
  quantity: string
  notional: string
  buyer_is_maker: boolean | null
  source: string
  received_at_ms: number
}

export type OhlcvBar = {
  instrument_id: string
  symbol: string
  interval_minutes: number
  start_ms: number
  end_ms: number
  open: string
  high: string
  low: string
  close: string
  volume: string
  trade_count: number
  is_closed: boolean
  source: string
  updated_at_ms: number
}
