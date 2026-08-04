import type { AggTrade, EquityPoint, EventItem, Fill, FundingPayment, LiveReadiness, LiveSession, OhlcvBar, Order, Overview, ReturnSummary, WarehouseSummary } from './types'

export class ApiError extends Error {
  status: number

  constructor(status: number) {
    super(`API ${status}`)
    this.status = status
  }
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin' })
  if (!response.ok) throw new ApiError(response.status)
  return response.json() as Promise<T>
}

async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw new ApiError(response.status)
  return response.json() as Promise<T>
}

export const api = {
  overview: () => getJson<Overview>('/api/overview'),
  liveReadiness: () => getJson<LiveReadiness>('/api/live/readiness'),
  liveSession: () => getJson<LiveSession>('/api/live/session'),
  unlockLive: (token: string) => postJson<{ ok: boolean; authenticated: boolean }>('/api/live/unlock', { token }),
  unlockLiveLocal: () => postJson<{ ok: boolean; authenticated: boolean }>('/api/live/unlock-local'),
  logoutLive: () => postJson<{ ok: boolean; authenticated: boolean }>('/api/live/logout'),
  liveOverview: () => getJson<Overview>('/api/live/overview'),
  liveEquity: (_accountId: string, beforeMs?: number) => {
    const cursor = beforeMs === undefined ? '' : `&before_ms=${beforeMs}`
    return getJson<EquityPoint[]>(`/api/live/equity?limit=2000${cursor}`)
  },
  liveReturns: (_accountId: string) => {
    const offset = -new Date().getTimezoneOffset()
    return getJson<ReturnSummary>(`/api/live/returns?timezone_offset_minutes=${offset}`)
  },
  liveFills: () => getJson<Fill[]>('/api/live/fills?limit=200'),
  liveOrders: () => getJson<Order[]>('/api/live/orders?limit=200'),
  equity: (accountId: string, beforeMs?: number) => {
    const cursor = beforeMs === undefined ? '' : `&before_ms=${beforeMs}`
    return getJson<EquityPoint[]>(`/api/accounts/${accountId}/equity?limit=2000${cursor}`)
  },
  returns: (accountId: string) => {
    const offset = -new Date().getTimezoneOffset()
    return getJson<ReturnSummary>(`/api/accounts/${accountId}/returns?timezone_offset_minutes=${offset}`)
  },
  fills: (accountId = 'soxlb') => getJson<Fill[]>(`/api/fills?account_id=${accountId}&limit=200`),
  orders: (accountId = 'soxlb') => getJson<Order[]>(`/api/orders?account_id=${accountId}&limit=200`),
  events: (accountId = 'soxlb') => getJson<EventItem[]>(`/api/events?account_id=${accountId}&limit=200`),
  funding: (accountId: string) =>
    getJson<FundingPayment[]>(`/api/funding?account_id=${accountId}&limit=1000`),
  warehouse: () => getJson<WarehouseSummary>('/api/warehouse'),
  aggTrades: (instrumentId = 'soxlb') =>
    getJson<AggTrade[]>(`/api/market/agg-trades?instrument_id=${instrumentId}&limit=100`),
  ohlcv: (instrumentId = 'soxlb', beforeMs?: number) => {
    const cursor = beforeMs === undefined ? '' : `&before_ms=${beforeMs}`
    return getJson<OhlcvBar[]>(`/api/market/ohlcv?instrument_id=${instrumentId}&limit=200${cursor}`)
  },
  control: async (action: 'pause' | 'resume') => {
    return postJson<{ ok: boolean; trading_enabled: boolean }>('/api/control', { action })
  },
}
