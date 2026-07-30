export type StrategyView = {
  ready: boolean
  atr: string | null
  trailing_stop: string | null
  price: string | null
  relation: 'above' | 'below' | 'warming'
  bar_start_ms: number | null
  bought_this_bar: boolean
  flattened_this_bar: boolean
}

export type Runtime = {
  id: string
  symbol: string
  display_symbol: string
  name: string
  venue: string
  asset_type: string
  reference_symbol: string
  feed: string
  status: string
  status_message: string
  reconnects: number
  last_tick: { timestamp_ms: number; price: string; quantity: string; source: string } | null
  strategy: StrategyView
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
  equity: string
  total_pnl: string
  total_return: number
  max_drawdown: number
  last_price: string | null
  last_snapshot_ms: number | null
  unrealized_pnl: string
  market_value: string
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
