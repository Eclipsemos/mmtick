export type StrategyView = {
  ready: boolean
  atr: string | null
  trailing_stop: string | null
  price: string | null
  relation: 'above' | 'below' | 'warming'
  bar_start_ms: number | null
  bought_this_bar: boolean
  flattened_this_bar: boolean
  last_cross: 'UP' | 'DOWN' | null
  last_cross_at_ms: number | null
  last_cross_result: 'BUY_SIGNAL' | 'SELL_SIGNAL' | 'BLOCKED' | null
  last_cross_reason: string | null
}

export type DecisionView = {
  state: 'PAUSED' | 'WARMING_UP' | 'ORDER_PENDING' | 'HOLDING_LONG' | 'REENTRY_LOCKED' | 'BUY_LOCKED' | 'ARMED_FOR_BUY' | 'WAITING_FOR_RESET'
  reason: string
  next_trigger: string
  trading_enabled: boolean
  has_position: boolean
  has_pending_order: boolean
  strategy_ready: boolean
  buy_lock_open: boolean
  reentry_lock_open: boolean
  fresh_up_cross: boolean
  bar_end_ms: number | null
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
  leverage: number
  margin_mode: string
  position_fraction: number
  fee_bps: number
  slippage_bps: number
  feed: string
  market_state: {
    mark_price?: string | null
    index_price?: string | null
    funding_rate?: string | null
    next_funding_time_ms?: number | null
    updated_at_ms?: number | null
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
    position_fraction: number
    fee_bps: number
    slippage_bps: number
  }
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
  atr: string
  trailing_stop: string
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
