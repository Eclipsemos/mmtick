import type { AggTrade, EquityPoint, EventItem, Fill, FundingPayment, OhlcvBar, Order, Overview, WarehouseSummary } from './types'

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`API ${response.status}`)
  return response.json() as Promise<T>
}

export const api = {
  overview: () => getJson<Overview>('/api/overview'),
  equity: (accountId: string) =>
    getJson<EquityPoint[]>(`/api/accounts/${accountId}/equity?limit=2000`),
  fills: (accountId = 'soxlb') => getJson<Fill[]>(`/api/fills?account_id=${accountId}&limit=200`),
  orders: (accountId = 'soxlb') => getJson<Order[]>(`/api/orders?account_id=${accountId}&limit=200`),
  events: (accountId = 'soxlb') => getJson<EventItem[]>(`/api/events?account_id=${accountId}&limit=200`),
  funding: (accountId: string) =>
    getJson<FundingPayment[]>(`/api/funding?account_id=${accountId}&limit=1000`),
  warehouse: () => getJson<WarehouseSummary>('/api/warehouse'),
  aggTrades: (instrumentId = 'soxlb') =>
    getJson<AggTrade[]>(`/api/market/agg-trades?instrument_id=${instrumentId}&limit=100`),
  ohlcv: (instrumentId = 'soxlb') =>
    getJson<OhlcvBar[]>(`/api/market/ohlcv?instrument_id=${instrumentId}&limit=100`),
  control: async (action: 'pause' | 'resume') => {
    const response = await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
    if (!response.ok) throw new Error(`API ${response.status}`)
    return response.json() as Promise<{ ok: boolean; trading_enabled: boolean }>
  },
}
