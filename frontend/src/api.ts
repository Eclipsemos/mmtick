import type { AggTrade, DeepFactorReport, EquityPoint, EventItem, Fill, FundingPayment, LiveReadiness, LiveSession, OhlcvBar, Order, Overview, ResearchBacktestRequest, ResearchDataStatus, ResearchFactorReport, ResearchFactorReportSummary, ResearchInstrumentId, ResearchJob, ResearchPreset, ResearchReport, ResearchReportSummary, ReturnSummary, WarehouseSummary } from './types'

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail?: unknown) {
    super(`API ${status}`)
    this.status = status
    this.detail = detail
  }
}

async function apiError(response: Response) {
  let detail: unknown
  try {
    detail = (await response.json() as { detail?: unknown }).detail
  } catch {
    detail = undefined
  }
  return new ApiError(response.status, detail)
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin' })
  if (!response.ok) throw await apiError(response)
  return response.json() as Promise<T>
}

async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw await apiError(response)
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
  liveFunding: () => getJson<FundingPayment[]>('/api/live/funding?limit=1000'),
  stopLiveStrategy: () =>
    postJson<{ ok: boolean; strategy_paused: boolean; order_submission_ready: boolean }>(
      '/api/live/control',
      { action: 'stop' },
    ),
  liveFlatten: () => postJson<{
    ok: boolean
    already_flat: boolean
    flat_confirmed: boolean
    orders: Array<{
      client_order_id: string
      side: string
      position_side: string
      quantity: string
      status: string
    }>
  }>('/api/live/flatten', { confirm: 'FLATTEN_SOXLUSDT' }),
  equity: (accountId: string, beforeMs?: number) => {
    const cursor = beforeMs === undefined ? '' : `&before_ms=${beforeMs}`
    return getJson<EquityPoint[]>(`/api/accounts/${accountId}/equity?limit=2000${cursor}`)
  },
  returns: (accountId: string) => {
    const offset = -new Date().getTimezoneOffset()
    return getJson<ReturnSummary>(`/api/accounts/${accountId}/returns?timezone_offset_minutes=${offset}`)
  },
  fills: (accountId = 'soxl_perp') => getJson<Fill[]>(`/api/fills?account_id=${accountId}&limit=200`),
  orders: (accountId = 'soxl_perp') => getJson<Order[]>(`/api/orders?account_id=${accountId}&limit=200`),
  events: (accountId = 'soxl_perp') => getJson<EventItem[]>(`/api/events?account_id=${accountId}&limit=200`),
  funding: (accountId: string) =>
    getJson<FundingPayment[]>(`/api/funding?account_id=${accountId}&limit=1000`),
  warehouse: () => getJson<WarehouseSummary>('/api/warehouse'),
  aggTrades: (instrumentId = 'soxl_perp') =>
    getJson<AggTrade[]>(`/api/market/agg-trades?instrument_id=${instrumentId}&limit=100`),
  ohlcv: (instrumentId = 'soxl_perp', beforeMs?: number) => {
    const cursor = beforeMs === undefined ? '' : `&before_ms=${beforeMs}`
    return getJson<OhlcvBar[]>(`/api/market/ohlcv?instrument_id=${instrumentId}&limit=200${cursor}`)
  },
  control: async (action: 'pause' | 'resume') => {
    return postJson<{ ok: boolean; trading_enabled: boolean }>('/api/control', { action })
  },
  researchPresets: () => getJson<ResearchPreset[]>('/api/research/presets'),
  researchDataStatus: (instrumentId: ResearchInstrumentId) =>
    getJson<ResearchDataStatus>(`/api/research/data-status?instrument_id=${instrumentId}`),
  updateResearchData: ({ instrumentId, targetDate }: { instrumentId: ResearchInstrumentId, targetDate: string }) =>
    postJson<ResearchJob>('/api/research/data-update', { instrument_id: instrumentId, target_date: targetDate }),
  runResearchBacktest: (request: ResearchBacktestRequest) =>
    postJson<ResearchJob>('/api/research/backtests', request),
  runFactorMining: (instrumentId: ResearchInstrumentId = 'btc_perp') =>
    postJson<ResearchJob>('/api/research/factor-mining', { instrument_id: instrumentId }),
  runDeepFactorMining: (instruments: ResearchInstrumentId[] = ['btc_perp', 'eth_perp']) =>
    postJson<ResearchJob>('/api/research/deep-factor', { instruments }),
  researchJob: (jobId: string) => getJson<ResearchJob>(`/api/research/jobs/${jobId}`),
  researchReports: (instrumentId: ResearchInstrumentId) =>
    getJson<ResearchReportSummary[]>(`/api/research/reports?instrument_id=${instrumentId}`),
  researchReport: (reportId: string) =>
    getJson<ResearchReport>(`/api/research/reports/${reportId}`),
  researchFactorReport: (reportId: string) =>
    getJson<ResearchFactorReport>(`/api/research/factor-reports/${reportId}`),
  researchFactorReports: (instrumentId: ResearchInstrumentId = 'btc_perp') =>
    getJson<ResearchFactorReportSummary[]>(`/api/research/factor-reports?instrument_id=${instrumentId}`),
  researchDeepFactorReports: () =>
    getJson<Array<{ id: string, generated_at: string, instruments: string[], status: string }>>('/api/research/deep-factor-reports'),
  researchDeepFactorReport: (reportId: string) =>
    getJson<DeepFactorReport>(`/api/research/deep-factor-reports/${reportId}`),
}
