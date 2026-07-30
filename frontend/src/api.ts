import type { EquityPoint, EventItem, Fill, Order, Overview } from './types'

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`API ${response.status}`)
  return response.json() as Promise<T>
}

export const api = {
  overview: () => getJson<Overview>('/api/overview'),
  equity: (accountId: string) =>
    getJson<EquityPoint[]>(`/api/accounts/${accountId}/equity?limit=2000`),
  fills: () => getJson<Fill[]>('/api/fills?limit=200'),
  orders: () => getJson<Order[]>('/api/orders?limit=200'),
  events: () => getJson<EventItem[]>('/api/events?limit=200'),
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

