import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ArrowDownToLine,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  CircleArrowDown,
  CircleArrowUp,
  CirclePause,
  CirclePlay,
  CircleX,
  Clock3,
  Crosshair,
  Database,
  Eye,
  EyeOff,
  Gauge,
  History,
  ListOrdered,
  LockKeyhole,
  Maximize2,
  Radio,
  RefreshCw,
  ShieldCheck,
  Target,
  TrendingDown,
  TrendingUp,
  WalletCards,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Brush,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, ApiError } from './api'
import {
  getDailySnapshot,
  millisecondsUntilNextUtcDay,
  readDailySnapshot,
  readLatestDailySnapshot,
  utcSnapshotDay,
} from './dailySnapshot'
import type {
  Account,
  AggTrade,
  EquityPoint,
  Fill,
  FundingPayment,
  OhlcvBar,
  Order,
  PortfolioLedgerDay,
  PortfolioSleeveComponent,
  PortfolioSleeveEvent,
  ReturnPeriod,
  ReturnSummary,
  WarehouseSummary,
  LiveReadiness,
} from './types'

type View = 'monitor' | 'orders' | 'returns' | 'warehouse'
type AccountMode = 'paper' | 'live'

type PricePoint = {
  timestamp: number
  price: number
  trailingStop: number | null
}

type TradePoint = {
  id: string
  timestamp: number
  price: number
  side: 'BUY' | 'SELL'
  action: 'LONG' | 'SHORT' | 'CLOSE'
}

type CompletedTrade = {
  direction: 'LONG' | 'SHORT'
  entryTimestamp: number
  exitTimestamp: number
  entryPrice: number
  exitPrice: number
  quantity: number
  fees: number
  fundingPnl: number
  netPnl: number
  returnPercent: number
}

type ChartRange = {
  startIndex: number
  endIndex: number
}

const DEFAULT_VISIBLE_POINTS = 500
const DEFAULT_VISIBLE_KLINES = 60
const MIN_VISIBLE_POINTS = 20
const CHART_COLORS = {
  grid: '#e3e9ed',
  axis: '#687782',
  profit: '#078a61',
  loss: '#d74750',
  close: '#596a75',
  price: '#25313a',
  atr: '#d39a18',
  canvas: '#ffffff',
  brush: '#edf2f4',
  brushStroke: '#87959f',
}

function money(value: string | number | null, currency = 'USD') {
  if (value === null) return '--'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency === 'USDT' ? 'USD' : currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value))
}

function percent(value: number) {
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

function rate(value: number) {
  return `${(value * 100).toFixed(2)}%`
}

function returnValue(value: number | null) {
  return value === null ? '--' : percent(value)
}

function number(value: string | number | null, digits = 3) {
  if (value === null) return '--'
  return Number(value).toLocaleString('en-US', { maximumFractionDigits: digits })
}

function bytes(value: number) {
  const units = ['B', 'KB', 'MB', 'GB']
  let amount = value
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit += 1
  }
  return `${amount.toLocaleString('en-US', { maximumFractionDigits: unit ? 2 : 0 })} ${units[unit]}`
}

function time(value: number | null) {
  if (!value) return '--'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(value)
}

function mergeTimeSeries<T>(
  older: T[],
  latest: T[],
  getTimestamp: (item: T) => number,
) {
  const byTimestamp = new Map<number, T>()
  for (const item of older) byTimestamp.set(getTimestamp(item), item)
  for (const item of latest) byTimestamp.set(getTimestamp(item), item)
  return [...byTimestamp.values()].sort((left, right) => getTimestamp(left) - getTimestamp(right))
}

function useTimePaginatedSeries<T>(
  scope: string,
  latest: T[],
  getTimestamp: (item: T) => number,
  fetchPage: (scope: string, beforeMs?: number) => Promise<T[]>,
  pageSize: number,
) {
  const [olderRows, setOlderRows] = useState<T[]>([])
  const [isLoadingOlder, setIsLoadingOlder] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const activeScope = useRef(scope)
  const rows = useMemo(
    () => mergeTimeSeries(olderRows, latest, getTimestamp),
    [getTimestamp, latest, olderRows],
  )

  useEffect(() => {
    activeScope.current = scope
    setOlderRows([])
    setHasMore(true)
    setIsLoadingOlder(false)
  }, [scope])

  const loadOlder = useCallback(async () => {
    if (isLoadingOlder || !hasMore || rows.length === 0) return
    const requestedScope = scope
    const beforeMs = getTimestamp(rows[0])
    setIsLoadingOlder(true)
    try {
      const page = await fetchPage(requestedScope, beforeMs)
      if (activeScope.current !== requestedScope) return
      setOlderRows((current) => mergeTimeSeries(current, page, getTimestamp))
      setHasMore(page.length >= pageSize)
    } finally {
      if (activeScope.current === requestedScope) setIsLoadingOlder(false)
    }
  }, [fetchPage, getTimestamp, hasMore, isLoadingOlder, pageSize, rows, scope])

  return { rows, loadOlder, isLoadingOlder, hasMore }
}

const equityTimestamp = (point: EquityPoint) => point.timestamp_ms
const ohlcvTimestamp = (bar: OhlcvBar) => bar.start_ms

function useUtcSnapshotDay() {
  const [utcDay, setUtcDay] = useState(() => utcSnapshotDay())

  useEffect(() => {
    let timer = 0
    const scheduleNextDay = () => {
      timer = window.setTimeout(() => {
        setUtcDay(utcSnapshotDay())
        scheduleNextDay()
      }, millisecondsUntilNextUtcDay() + 50)
    }
    scheduleNextDay()
    return () => window.clearTimeout(timer)
  }, [])

  return utcDay
}

function returnsSnapshotScope(accountId: string) {
  return `returns:${accountId}:tz${-new Date().getTimezoneOffset()}`
}

function App() {
  const client = useQueryClient()
  const [view, setView] = useState<View>('monitor')
  const [mode, setMode] = useState<AccountMode>('paper')
  const [paperAccountId, setPaperAccountId] = useState('soxl_perp_long')
  const [unlockOpen, setUnlockOpen] = useState(false)
  const [unlockToken, setUnlockToken] = useState('')
  const [unlockError, setUnlockError] = useState('')
  const [switchingMode, setSwitchingMode] = useState(false)
  const [flattenOpen, setFlattenOpen] = useState(false)
  const [flattenError, setFlattenError] = useState('')
  const [resumeOpen, setResumeOpen] = useState(false)
  const [resumeError, setResumeError] = useState('')
  const [liveActionMessage, setLiveActionMessage] = useState('')
  const utcDay = useUtcSnapshotDay()
  const accountId = mode === 'live' ? 'soxl_perp_live' : paperAccountId
  const overview = useQuery({
    queryKey: ['overview', mode],
    queryFn: mode === 'live' ? api.liveOverview : api.overview,
    refetchInterval: 1000,
  })
  const selectedOverviewAccount = overview.data?.accounts.find((item) => item.id === accountId)
  const marketSelectionReady = mode === 'live' || Boolean(selectedOverviewAccount)
  const marketInstrumentId = mode === 'live'
    ? 'soxl_perp'
    : selectedOverviewAccount?.runtime.market_data_id ?? accountId
  const marketIntervalMinutes = selectedOverviewAccount?.runtime.strategy_config.bar_minutes ?? 15
  const isPortfolioAccount = mode === 'paper'
    && selectedOverviewAccount?.runtime.paper_model === 'portfolio'
  const liveReadiness = useQuery({
    queryKey: ['live-readiness'],
    queryFn: api.liveReadiness,
    refetchInterval: 5000,
  })
  const equityPage = useCallback(
    (_scope: string, beforeMs?: number) => mode === 'live'
      ? api.liveEquity(accountId, beforeMs)
      : api.equity(accountId, beforeMs),
    [accountId, mode],
  )
  const ohlcvPage = useCallback(
    (_scope: string, beforeMs?: number) => api.ohlcv(marketInstrumentId, beforeMs, marketIntervalMinutes),
    [marketInstrumentId, marketIntervalMinutes],
  )
  const equity = useQuery({
    queryKey: ['equity', mode, accountId],
    queryFn: () => equityPage(accountId),
    refetchInterval: 5000,
  })
  const fills = useQuery({
    queryKey: ['fills', mode, accountId],
    queryFn: () => mode === 'live' ? api.liveFills() : api.fills(accountId),
    refetchInterval: 5000,
  })
  const funding = useQuery({
    queryKey: ['funding', mode, accountId],
    queryFn: () => mode === 'live' ? api.liveFunding() : api.funding(accountId),
    refetchInterval: 5000,
  })
  const orders = useQuery({
    queryKey: ['orders', mode, accountId],
    queryFn: () => mode === 'live' ? api.liveOrders() : api.orders(accountId),
    refetchInterval: 5000,
    enabled: view === 'orders' && !isPortfolioAccount,
  })
  const portfolioSleeveEvents = useQuery({
    queryKey: ['portfolio-sleeve-events', accountId, 'base'],
    queryFn: () => api.portfolioSleeveEvents(accountId),
    enabled: view === 'orders' && isPortfolioAccount,
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  const returns = useQuery({
    queryKey: ['returns', mode, accountId, utcDay],
    queryFn: () => mode === 'live'
      ? api.liveReturns(accountId)
      : getDailySnapshot(
          returnsSnapshotScope(accountId),
          utcDay,
          () => api.returns(accountId),
        ),
    initialData: mode === 'paper'
      ? () => readDailySnapshot<ReturnSummary>(returnsSnapshotScope(accountId), utcDay)
      : undefined,
    placeholderData: mode === 'paper'
      ? () => readLatestDailySnapshot<ReturnSummary>(returnsSnapshotScope(accountId))
      : undefined,
    enabled: view === 'monitor' || view === 'returns',
    staleTime: Infinity,
  })
  const warehouse = useQuery({
    queryKey: ['warehouse', utcDay],
    queryFn: () => getDailySnapshot('warehouse', utcDay, api.warehouse),
    initialData: () => readDailySnapshot<WarehouseSummary>('warehouse', utcDay),
    placeholderData: () => readLatestDailySnapshot<WarehouseSummary>('warehouse'),
    enabled: view === 'warehouse',
    staleTime: Infinity,
  })
  const aggTrades = useQuery({
    queryKey: ['agg-trades', mode, marketInstrumentId],
    queryFn: () => api.aggTrades(marketInstrumentId),
    enabled: view === 'warehouse' && marketSelectionReady,
  })
  const ohlcv = useQuery({
    queryKey: ['ohlcv', mode, marketInstrumentId, marketIntervalMinutes],
    queryFn: () => api.ohlcv(marketInstrumentId, undefined, marketIntervalMinutes),
    enabled: marketSelectionReady,
    refetchInterval: 5000,
  })
  const portfolioLedger = useQuery({
    queryKey: ['portfolio-ledger', accountId, 'base'],
    queryFn: () => api.portfolioLedger(accountId),
    enabled: mode === 'paper'
      && view === 'monitor'
      && selectedOverviewAccount?.runtime.paper_model === 'portfolio',
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  const equityHistory = useTimePaginatedSeries(
    `${mode}:${accountId}`,
    equity.data ?? [],
    equityTimestamp,
    equityPage,
    2000,
  )
  const ohlcvHistory = useTimePaginatedSeries(
    `${mode}:${marketInstrumentId}:${marketIntervalMinutes}`,
    ohlcv.data ?? [],
    ohlcvTimestamp,
    ohlcvPage,
    200,
  )
  const control = useMutation({
    mutationFn: api.control,
    onSuccess: () => client.invalidateQueries({ queryKey: ['overview', 'paper'] }),
  })
  const refreshLiveAccount = useCallback(() => {
    void client.invalidateQueries({ queryKey: ['overview', 'live'] })
    void client.invalidateQueries({ queryKey: ['live-readiness'] })
    void client.invalidateQueries({ queryKey: ['orders', 'live'] })
    void client.invalidateQueries({ queryKey: ['fills', 'live'] })
    void client.invalidateQueries({ queryKey: ['equity', 'live'] })
  }, [client])
  const liveStrategyControl = useMutation({
    mutationFn: api.controlLiveStrategy,
    onSuccess: (result) => {
      setResumeOpen(false)
      setResumeError('')
      setLiveActionMessage(
        result.strategy_paused
          ? '策略已停止，重启服务后仍保持停止。'
          : '策略已启动，等待新的有效 ATR 入场信号。',
      )
      refreshLiveAccount()
    },
    onError: (error, action) => {
      const detail = error instanceof ApiError && error.detail && typeof error.detail === 'object'
        ? (error.detail as { message?: string }).message
        : undefined
      if (action === 'resume') {
        setResumeError(detail ?? '启动失败；请检查 Binance 对账和 LIVE 门禁状态。')
      } else {
        setLiveActionMessage('策略控制失败，请检查 LIVE 会话和服务状态。')
      }
    },
  })
  const liveFlatten = useMutation({
    mutationFn: api.liveFlatten,
    onSuccess: (result) => {
      setFlattenOpen(false)
      setFlattenError('')
      setLiveActionMessage(
        result.already_flat
          ? 'Binance 当前已无持仓。'
          : result.flat_confirmed
            ? '平仓已成交，正在刷新账户状态。'
            : '平仓指令已提交，请等待账户对账。',
      )
      refreshLiveAccount()
    },
    onError: (error) => {
      const detail = error instanceof ApiError && error.detail && typeof error.detail === 'object'
        ? (error.detail as { message?: string }).message
        : undefined
      setFlattenError(detail ?? '平仓失败；请检查是否存在挂单、未决订单或账户权限异常。')
    },
  })

  const showLiveUnlock = useCallback((message = '') => {
    setUnlockToken('')
    setUnlockError(message)
    setUnlockOpen(true)
  }, [])

  const switchToLive = useCallback(async () => {
    if (mode === 'live' || switchingMode) return
    setSwitchingMode(true)
    try {
      const session = await api.liveSession()
      if (session.authenticated) {
        setMode('live')
        return
      }
      if (!session.configured) {
        showLiveUnlock('服务端尚未配置 LIVE 操作员令牌。')
        return
      }
      if (session.local_unlock_available) {
        await api.unlockLiveLocal()
        setMode('live')
        return
      }
      showLiveUnlock()
    } catch {
      showLiveUnlock('无法建立 LIVE 会话，请检查服务状态。')
    } finally {
      setSwitchingMode(false)
    }
  }, [mode, showLiveUnlock, switchingMode])

  const submitLiveUnlock = useCallback(async () => {
    if (!unlockToken || switchingMode) return
    setSwitchingMode(true)
    setUnlockError('')
    try {
      await api.unlockLive(unlockToken)
      setUnlockToken('')
      setUnlockOpen(false)
      setMode('live')
    } catch (error) {
      setUnlockError(error instanceof ApiError && error.status === 401
        ? '操作员令牌不正确。'
        : 'LIVE 会话建立失败。')
    } finally {
      setSwitchingMode(false)
    }
  }, [switchingMode, unlockToken])

  const lockLive = useCallback(async () => {
    try {
      await api.logoutLive()
    } finally {
      setMode('paper')
      client.removeQueries({ queryKey: ['overview', 'live'] })
    }
  }, [client])

  useEffect(() => {
    if (mode === 'live' && overview.error instanceof ApiError && overview.error.status === 401) {
      setMode('paper')
      showLiveUnlock('LIVE 会话已过期，请重新验证。')
    }
  }, [mode, overview.error, showLiveUnlock])

  const accounts = overview.data?.accounts ?? []
  const account = accounts.find((item) => item.id === accountId) ?? accounts[0]
  const liveHasPosition = Number(account?.quantity ?? 0) !== 0
    || Boolean(liveReadiness.data?.block_reasons.includes('SIMULTANEOUS_LONG_SHORT_POSITION'))
  const liveCount = overview.data?.instruments.filter((item) => item.status === 'LIVE').length ?? 0
  const instrumentCount = overview.data?.instruments.length ?? 1
  const feedConnected = mode === 'live'
    ? Boolean(liveReadiness.data?.last_reconciled_at_ms)
    : liveCount > 0
  const feedLabel = mode === 'live'
    ? liveReadiness.data?.status ?? 'STARTING'
    : `${liveCount}/${instrumentCount} LIVE`
  const pageTitle = view === 'warehouse'
    ? '数据仓库'
    : view === 'returns'
      ? '收益明细'
      : mode === 'live' ? '实盘交易' : '模拟交易'
  const pageKicker = view === 'warehouse'
    ? 'MARKET DATA / SQLITE WAL'
    : view === 'returns'
      ? 'PERFORMANCE / CALENDAR RETURNS'
      : 'SHORT-HORIZON EXECUTION / ATR TICK V3'

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mastermind">mastermind</span>
          <span className="brand-colon">:</span>
          <span>tick</span>
        </div>
        <nav className="nav-tabs" aria-label="主视图">
          <button className={view === 'monitor' ? 'active' : ''} onClick={() => setView('monitor')}>
            <Gauge size={16} />监控
          </button>
          <button className={view === 'orders' ? 'active' : ''} onClick={() => setView('orders')}>
            <ListOrdered size={16} />订单
          </button>
          <button className={view === 'returns' ? 'active' : ''} onClick={() => setView('returns')}>
            <CalendarDays size={16} />收益明细
          </button>
          <button className={view === 'warehouse' ? 'active' : ''} onClick={() => setView('warehouse')}>
            <Database size={16} />仓库
          </button>
        </nav>
        <div className="top-actions">
          <div className="mode-switch" aria-label="账户模式">
            <button type="button" className={mode === 'paper' ? 'active paper' : ''} onClick={() => setMode('paper')}>PAPER</button>
            <button type="button" className={mode === 'live' ? 'active live' : ''} disabled={switchingMode} onClick={() => void switchToLive()}>LIVE</button>
          </div>
          <span className={`feed-state ${feedConnected ? 'live' : ''}`}>
            <i />{feedLabel}
          </span>
          {mode === 'paper' ? (
            <button
              className={overview.data?.trading_enabled ? 'control danger' : 'control resume'}
              disabled={control.isPending}
              onClick={() => control.mutate(overview.data?.trading_enabled ? 'pause' : 'resume')}
              title={overview.data?.trading_enabled ? '暂停策略' : '恢复策略'}
            >
              {overview.data?.trading_enabled ? <CirclePause size={17} /> : <CirclePlay size={17} />}
              {overview.data?.trading_enabled ? '暂停' : '恢复'}
            </button>
          ) : (
            <button className="control" type="button" onClick={() => void lockLive()} title="锁定实盘账户信息">
              <LockKeyhole size={16} />锁定
            </button>
          )}
        </div>
      </header>

      <main>
        <section className="page-head">
          <div>
            <div className="kicker">{pageKicker}</div>
            <h1>{pageTitle}</h1>
          </div>
          <div className="page-actions">
            <div className="account-switch" aria-label={mode === 'live' ? '实盘账户' : '模拟账户'}>
              {accounts.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={item.id === account?.id ? 'active' : ''}
                  onClick={() => mode === 'paper' && setPaperAccountId(item.id)}
                >
                  {item.display_symbol}
                </button>
              ))}
            </div>
            {mode === 'live' && (
              <div className="live-account-actions" aria-label="SOXL 合约实盘操作">
                <button
                  type="button"
                  className="control danger"
                  disabled={!liveHasPosition || liveFlatten.isPending}
                  onClick={() => { setFlattenError(''); setFlattenOpen(true) }}
                  title={liveHasPosition ? '按 Binance 实时仓位市价平仓' : '当前没有可平仓持仓'}
                >
                  <CircleX size={16} />平仓
                </button>
                <button
                  type="button"
                  className={liveReadiness.data?.persisted_paused ? 'control resume' : 'control danger'}
                  disabled={liveStrategyControl.isPending}
                  onClick={() => {
                    if (liveReadiness.data?.persisted_paused) {
                      setResumeError('')
                      setResumeOpen(true)
                    } else {
                      liveStrategyControl.mutate('stop')
                    }
                  }}
                  title={liveReadiness.data?.persisted_paused ? '重新启用实盘策略订单' : '持久停止策略信号执行'}
                >
                  {liveReadiness.data?.persisted_paused ? <CirclePlay size={16} /> : <CirclePause size={16} />}
                  {liveReadiness.data?.persisted_paused ? '启动策略' : '停止策略'}
                </button>
              </div>
            )}
            <div className="scope-chips">
              <span>{account?.symbol ?? 'INITIALIZING'}</span><span>{account?.runtime.paper_model === 'portfolio' ? '1D' : '15m'}</span><span>{view === 'warehouse' ? 'TICK ARCHIVE' : view === 'returns' ? 'PERFORMANCE' : account?.runtime.paper_model === 'portfolio' ? 'PORTFOLIO' : account?.runtime.allow_short ? 'LONG / SHORT' : 'LONG ONLY'}</span>
            </div>
          </div>
        </section>

        {mode === 'live' && <LiveReadinessBand readiness={liveReadiness.data} />}
        {mode === 'live' && liveActionMessage && (
          <div className="live-action-message" role="status">
            <span>{liveActionMessage}</span>
            <button type="button" aria-label="关闭操作提示" onClick={() => setLiveActionMessage('')}><CircleX size={15} /></button>
          </div>
        )}

        {overview.isError ? (
          <div className="error-state">
            API 无法连接
            <button onClick={() => overview.refetch()}><RefreshCw size={15} />重试</button>
          </div>
        ) : view === 'monitor' ? (
          <Monitor
            account={account}
            allowStrategyParameters={mode === 'live'}
            fillsExportUrl={mode === 'live' ? '/api/live/fills.csv' : `/api/fills.csv?account_id=${account?.id ?? ''}`}
            equity={equityHistory.rows}
            returnSummary={returns.data}
            fills={fills.data ?? []}
            funding={funding.data ?? []}
            bars={ohlcvHistory.rows}
            portfolioLedger={portfolioLedger.data ?? []}
            portfolioLedgerLoading={portfolioLedger.isLoading}
            loadOlderEquity={equityHistory.loadOlder}
            loadingOlderEquity={equityHistory.isLoadingOlder}
            hasOlderEquity={equityHistory.hasMore}
            loadOlderOhlcv={ohlcvHistory.loadOlder}
            loadingOlderOhlcv={ohlcvHistory.isLoadingOlder}
            hasOlderOhlcv={ohlcvHistory.hasMore}
          />
        ) : view === 'orders' ? (
          isPortfolioAccount ? (
            <PortfolioOrders events={portfolioSleeveEvents.data ?? []} loading={portfolioSleeveEvents.isLoading} />
          ) : (
            <Orders rows={orders.data ?? []} />
          )
        ) : view === 'returns' ? (
          <Returns account={account} summary={returns.data} loading={returns.isLoading} />
        ) : (
          <Warehouse
            summary={warehouse.data}
            bars={ohlcv.data ?? []}
            trades={aggTrades.data ?? []}
            instrumentId={marketInstrumentId}
          />
        )}
      </main>
      <footer>
        <span>mastermind:tick v0.1</span>
        <span>{mode === 'live' ? 'Binance USD-M Futures 实盘账户 · 受保护操作' : '本地模拟撮合 · 非真实账户'}</span>
      </footer>
      {unlockOpen && (
        <LiveUnlockDialog
          token={unlockToken}
          error={unlockError}
          pending={switchingMode}
          onTokenChange={setUnlockToken}
          onCancel={() => { setUnlockOpen(false); setUnlockToken(''); setUnlockError('') }}
          onSubmit={() => void submitLiveUnlock()}
        />
      )}
      {flattenOpen && (
        <LiveFlattenDialog
          error={flattenError}
          pending={liveFlatten.isPending}
          onCancel={() => { if (!liveFlatten.isPending) { setFlattenOpen(false); setFlattenError('') } }}
          onConfirm={() => liveFlatten.mutate()}
        />
      )}
      {resumeOpen && (
        <LiveResumeDialog
          error={resumeError}
          pending={liveStrategyControl.isPending}
          onCancel={() => { if (!liveStrategyControl.isPending) { setResumeOpen(false); setResumeError('') } }}
          onConfirm={() => liveStrategyControl.mutate('resume')}
        />
      )}
    </div>
  )
}

function LiveResumeDialog({
  error,
  pending,
  onCancel,
  onConfirm,
}: {
  error: string
  pending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !pending && onCancel()}>
      <div className="unlock-dialog flatten-dialog" role="dialog" aria-modal="true" aria-labelledby="live-resume-title">
        <div className="unlock-dialog-head">
          <div><span>ENABLE ORDERS / REAL ACCOUNT</span><h2 id="live-resume-title">确认启动实盘策略</h2></div>
          <button type="button" disabled={pending} onClick={onCancel} title="关闭" aria-label="关闭"><CircleX size={19} /></button>
        </div>
        <form onSubmit={(event) => { event.preventDefault(); onConfirm() }}>
          <div className="flatten-dialog-copy">
            启动后将重新核验 Binance 账户、仓位、挂单和交易门禁，并允许策略提交真实订单。
            当前趋势不会被追入；策略等待恢复后的新有效 ATR 信号。
          </div>
          {error && <p role="alert">{error}</p>}
          <div className="unlock-dialog-actions">
            <button type="button" disabled={pending} onClick={onCancel}>取消</button>
            <button className="primary" type="submit" disabled={pending}>{pending ? '核验并启动中' : '确认启动'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function LiveFlattenDialog({
  error,
  pending,
  onCancel,
  onConfirm,
}: {
  error: string
  pending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !pending && onCancel()}>
      <div className="unlock-dialog flatten-dialog" role="dialog" aria-modal="true" aria-labelledby="live-flatten-title">
        <div className="unlock-dialog-head">
          <div><span>MARKET CLOSE / REAL ACCOUNT</span><h2 id="live-flatten-title">确认平仓</h2></div>
          <button type="button" disabled={pending} onClick={onCancel} title="关闭" aria-label="关闭"><CircleX size={19} /></button>
        </div>
        <form onSubmit={(event) => { event.preventDefault(); onConfirm() }}>
          <div className="flatten-dialog-copy">
            将按 Binance 实时查询到的数量，以市价平掉 SOXLUSDT 全部现有仓位。
            平仓可能产生滑点和手续费，并且不会自动停止策略。
          </div>
          {error && <p role="alert">{error}</p>}
          <div className="unlock-dialog-actions">
            <button type="button" disabled={pending} onClick={onCancel}>取消</button>
            <button className="danger-confirm" type="submit" disabled={pending}>{pending ? '平仓中' : '确认平仓'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function LiveUnlockDialog({
  token,
  error,
  pending,
  onTokenChange,
  onCancel,
  onSubmit,
}: {
  token: string
  error: string
  pending: boolean
  onTokenChange: (value: string) => void
  onCancel: () => void
  onSubmit: () => void
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onCancel()}>
      <div className="unlock-dialog" role="dialog" aria-modal="true" aria-labelledby="live-unlock-title">
        <div className="unlock-dialog-head">
          <div><span>PROTECTED ACCOUNT</span><h2 id="live-unlock-title">验证实盘访问</h2></div>
          <button type="button" onClick={onCancel} title="关闭" aria-label="关闭"><CircleX size={19} /></button>
        </div>
        <form onSubmit={(event) => { event.preventDefault(); onSubmit() }}>
          <label htmlFor="live-operator-token">操作员令牌</label>
          <input
            id="live-operator-token"
            type="password"
            value={token}
            autoFocus
            autoComplete="off"
            onChange={(event) => onTokenChange(event.target.value)}
          />
          {error && <p role="alert">{error}</p>}
          <div className="unlock-dialog-actions">
            <button type="button" onClick={onCancel}>取消</button>
            <button className="primary" type="submit" disabled={!token || pending}>{pending ? '验证中' : '进入 LIVE'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function LiveReadinessBand({ readiness }: { readiness?: LiveReadiness }) {
  const ready = readiness?.order_submission_ready ?? false
  const status = readiness?.status ?? 'STARTING'
  const reasons = readiness?.block_reasons ?? []
  const statusMessage = readiness?.persisted_paused
    ? '策略已由操作员持久停止；行情和签名对账继续运行'
    : {
    STARTING: '正在检查 Binance USD-M Futures 实盘执行条件',
    DISABLED: '实盘运行时已关闭，不会发送真实订单',
    BLOCKED: '签名对账未就绪，真实订单已阻断',
    OBSERVE_ONLY: '合约签名对账运行中，真实订单保持关闭',
    ARMED: '实盘执行已启用',
    STOPPED: '实盘运行时已停止',
    }[status]
  const blockLabels: Record<string, string> = {
    IP_RESTRICTION_DISABLED: '未启用 IP 白名单',
    SPOT_TRADING_PERMISSION_MISSING: 'API 仅有读取权限',
    FUTURES_TRADING_PERMISSION_MISSING: '缺少合约交易权限',
    WITHDRAWAL_PERMISSION_ENABLED: '必须关闭提现权限',
    UNKNOWN_OPEN_ORDERS: '存在未接管挂单',
    UNMANAGED_EXISTING_POSITION: '存在未接管 SOXL 合约持仓',
    CREDENTIALS_MISSING: '缺少 API 凭证',
    CREDENTIAL_FILE_PERMISSIONS_INSECURE: '凭证文件权限不安全',
    LEVERAGE_MISMATCH: `当前 ${readiness?.current_leverage ?? '--'}x，目标 ${readiness?.target_leverage ?? '--'}x`,
    MARGIN_MODE_MISMATCH: `当前 ${readiness?.current_margin_mode ?? '--'}，目标 ${readiness?.target_margin_mode ?? '--'}`,
    POSITION_MODE_MISMATCH: '持仓模式不匹配',
    MULTI_ASSET_MODE_ENABLED: 'Multi-Assets 模式已启用',
    SIMULTANEOUS_LONG_SHORT_POSITION: '同时存在多空持仓',
    OTHER_OPEN_POSITIONS: '账户存在其他合约持仓',
  }
  const reasonText = reasons.map((reason) => blockLabels[reason] ?? reason)
  const syncText = readiness?.last_reconciled_at_ms
    ? `最近对账 ${time(readiness.last_reconciled_at_ms)} · 已同步 ${readiness.synced_trade_count} 笔成交`
    : '等待签名账户对账'
  return (
    <section className={`live-readiness ${ready ? 'armed' : ''}`} aria-label="SOXL 合约实盘就绪状态">
      <div className="live-readiness-state">
        <ShieldCheck size={18} />
        <div><span>{readiness?.display_symbol ?? 'SOXL PERP LIVE'}</span><strong>{status}</strong></div>
      </div>
      <p>{statusMessage}</p>
      <div className="live-readiness-gates">
        <Gate open={readiness?.public_capability ?? false} label="公开接口" />
        <Gate open={readiness?.credentials_present ?? false} label="API 凭证" />
        <Gate open={readiness?.api_reading_enabled ?? false} label="只读权限" />
        <Gate open={readiness?.signed_account_verified ?? false} label="签名对账" />
        <Gate open={readiness?.trading_permitted ?? false} label="合约权限" />
        <Gate open={readiness?.ip_restricted ?? false} label="IP 白名单" />
        <Gate open={!(readiness?.withdrawals_enabled ?? true)} label="提现关闭" />
        <Gate open={readiness?.activation_confirmed ?? false} label="启动确认" />
        <Gate open={readiness?.allow_order_submission ?? false} label="订单开关" />
        <Gate open={!(readiness?.persisted_paused ?? false)} label="策略未停止" />
      </div>
      <small title={reasons.join(', ')}>{ready ? '真实订单已启用' : `${syncText}${reasonText.length ? ` · ${reasonText.join(' · ')}` : ''}`}</small>
    </section>
  )
}

function Monitor({
  account,
  allowStrategyParameters,
  fillsExportUrl,
  equity,
  returnSummary,
  fills,
  funding,
  bars,
  portfolioLedger,
  portfolioLedgerLoading,
  loadOlderEquity,
  loadingOlderEquity,
  hasOlderEquity,
  loadOlderOhlcv,
  loadingOlderOhlcv,
  hasOlderOhlcv,
}: {
  account?: Account
  allowStrategyParameters: boolean
  fillsExportUrl: string
  equity: EquityPoint[]
  returnSummary?: ReturnSummary
  fills: Fill[]
  funding: FundingPayment[]
  bars: OhlcvBar[]
  portfolioLedger: PortfolioLedgerDay[]
  portfolioLedgerLoading: boolean
  loadOlderEquity: () => Promise<void>
  loadingOlderEquity: boolean
  hasOlderEquity: boolean
  loadOlderOhlcv: () => Promise<void>
  loadingOlderOhlcv: boolean
  hasOlderOhlcv: boolean
}) {
  const accountFills = useMemo(
    () => fills.filter((fill) => fill.account_id === account?.id),
    [fills, account?.id],
  )
  const completedTrades = useMemo(
    () => buildCompletedTrades(accountFills, funding),
    [accountFills, funding],
  )
  const [showStrategyParameters, setShowStrategyParameters] = useState(false)
  useEffect(() => setShowStrategyParameters(false), [account?.id])
  const equityChart = useMemo(
    () => returnSummary?.daily.flatMap((period) => {
      if (period.equity === null || !Number.isFinite(Number(period.equity))) return []
      return [{
        timestamp: Math.min(period.end_ms - 1, returnSummary.as_of_ms),
        equity: Number(period.equity),
      }]
    }) ?? [],
    [returnSummary],
  )
  if (!account) return <div className="loading"><Activity size={20} />正在初始化账户</div>
  const runtime = account.runtime
  const strategy = runtime.strategy
  const positionQuantity = Number(account.quantity)
  const positionSide = positionQuantity > 0 ? '多头' : positionQuantity < 0 ? '空头' : '空仓'
  const positive = Number(account.total_pnl) >= 0
  const netCashFlow = Number(account.net_cash_flow ?? 0)
  const latestMarketBar = bars.reduce<OhlcvBar | undefined>(
    (latest, bar) => !latest || bar.end_ms > latest.end_ms ? bar : latest,
    undefined,
  )
  const displayPrice = runtime.paper_model === 'portfolio'
    ? latestMarketBar?.close ?? null
    : account.last_price
  const displayPriceAt = runtime.paper_model === 'portfolio'
    ? latestMarketBar?.end_ms ?? null
    : account.last_snapshot_ms
  const latestPortfolioDay = portfolioLedger[portfolioLedger.length - 1]
  const portfolioTargets = latestPortfolioDay
    ? aggregatePortfolioTargets(
        latestPortfolioDay,
        Number(runtime.market_state.effective_outer_exposure ?? latestPortfolioDay.state.outer_exposure),
      )
    : null

  return (
    <>
      <section className="instrument-band">
        <div className="symbol-block">
          <div className="symbol-line">
            <h2>{account.display_symbol}</h2>
            <span>{runtime.asset_type.replace('_', ' ')}</span>
          </div>
          <p>{runtime.name}</p>
        </div>
        <div className="quote-block">
          <strong>{money(displayPrice, account.currency)}</strong>
          <span><Clock3 size={13} />{time(displayPriceAt)}</span>
        </div>
        <div className="source-block">
          <span className={`runtime ${runtime.status.toLowerCase()}`}>
            <Radio size={14} />{runtime.status}
          </span>
          <strong>{runtime.feed}</strong>
          <small>{runtime.venue}</small>
        </div>
      </section>

      <section className="metrics-grid">
        <Metric label="账户净值" value={money(account.equity, account.currency)} sub={`初始 ${money(account.initial_cash, account.currency)}${netCashFlow ? ` · 净入金 ${money(netCashFlow, account.currency)}` : ''}`} icon={<WalletCards />} />
        <Metric label="累计收益" value={percent(account.total_return)} sub={money(account.total_pnl, account.currency)} tone={positive ? 'good' : 'bad'} icon={positive ? <TrendingUp /> : <TrendingDown />} />
        <Metric
          label="当前持仓"
          value={portfolioTargets ? `BTC ${signedExposure(portfolioTargets.btc)}` : number(Math.abs(positionQuantity))}
          sub={portfolioTargets ? `ETH ${signedExposure(portfolioTargets.eth)} · 日终模型目标` : `${positionSide} · 均价 ${money(account.average_price, account.currency)}`}
          icon={<Activity />}
        />
        <Metric label="最大回撤" value={percent(account.max_drawdown)} sub={`${account.round_trips} 次完整交易`} tone="bad" icon={<TrendingDown />} />
        <Metric label="夏普率" value={number(account.sharpe_ratio, 2)} sub={`${runtime.paper_model === 'portfolio' ? '日线' : '15m'} 年化 · rf 0%`} tone={account.sharpe_ratio === null ? '' : account.sharpe_ratio >= 0 ? 'good' : 'bad'} icon={<Gauge />} />
        <Metric label="轮次胜率" value={account.win_rate === null ? '--' : rate(account.win_rate)} sub={`${account.winning_trades} 赢 / ${account.round_trips} 轮`} tone={account.win_rate === null ? '' : account.win_rate >= 0.5 ? 'good' : 'bad'} icon={<Target />} />
      </section>

      <DecisionStatus runtime={runtime} />

      {runtime.paper_model === 'portfolio' && (
        <PortfolioAttribution runtime={runtime} ledger={portfolioLedger} loading={portfolioLedgerLoading} />
      )}

      <section className="workspace-grid">
        {runtime.paper_model === 'portfolio' ? (
          <OfficialKlinePanel
            validation={account.runtime.kline_state.validation}
            intervalMinutes={account.runtime.strategy_config.bar_minutes}
            bars={bars}
            fills={accountFills}
            paperModel={account.runtime.paper_model}
            loadOlder={loadOlderOhlcv}
            loadingOlder={loadingOlderOhlcv}
            hasOlder={hasOlderOhlcv}
          />
        ) : (
          <PriceSignalPanel
          account={account}
          equity={equity}
          fills={accountFills}
          loadOlder={loadOlderEquity}
          loadingOlder={loadingOlderEquity}
          hasOlder={hasOlderEquity}
          />
        )}

        <div className="panel strategy-panel">
          <div className="panel-head">
            <div><span>STRATEGY STATE</span><h3>{runtime.paper_model === 'portfolio' ? '月历组合状态' : 'ATR 实时状态'}</h3></div>
            <div className="strategy-head-actions">
              {allowStrategyParameters && (
                <button
                  type="button"
                  className="strategy-parameter-toggle"
                  aria-label={showStrategyParameters ? '隐藏策略参数' : '显示策略参数'}
                  aria-expanded={showStrategyParameters}
                  aria-controls="strategy-parameters"
                  title={showStrategyParameters ? '隐藏策略参数' : '显示策略参数'}
                  onClick={() => setShowStrategyParameters((current) => !current)}
                >
                  {showStrategyParameters ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              )}
              <span className={`relation ${strategy.relation}`}>{strategy.relation.toUpperCase()}</span>
            </div>
          </div>
          {allowStrategyParameters && showStrategyParameters && (
            <dl id="strategy-parameters" className="strategy-parameters" role="group" aria-label="当前策略参数">
              <div><dt>算法版本</dt><dd>{runtime.strategy_config.algorithm_version}</dd></div>
              <div><dt>K 线周期</dt><dd>{runtime.strategy_config.bar_minutes}m</dd></div>
              <div><dt>ATR 周期</dt><dd>{runtime.strategy_config.atr_period}</dd></div>
              <div><dt>ATR 倍数</dt><dd>{number(runtime.strategy_config.atr_multiplier, 2)}</dd></div>
              <div><dt>效率比周期</dt><dd>{runtime.strategy_config.trend_efficiency_period} 根</dd></div>
              <div><dt>最低趋势效率</dt><dd>{number(runtime.strategy_config.minimum_trend_efficiency, 2)}</dd></div>
              <div><dt>反向确认距离</dt><dd>{number(runtime.strategy_config.reversal_confirmation_atr, 2)} ATR</dd></div>
              {runtime.paper_model === 'futures' && <div><dt>利润保护激活</dt><dd>{number(runtime.strategy_config.profit_activation_atr ?? null, 2)} ATR</dd></div>}
              {runtime.strategy_config.continuation_reentry_atr != null && <div><dt>延续重入阈值</dt><dd>{number(runtime.strategy_config.continuation_reentry_atr, 2)} ATR</dd></div>}
              {runtime.paper_model === 'futures' && <div><dt>利润跟踪距离</dt><dd>{number(runtime.strategy_config.profit_trailing_atr ?? null, 2)} ATR</dd></div>}
              <div><dt>单 K 线动作</dt><dd>{runtime.strategy_config.one_action_per_bar ? '最多 1 次' : '--'}</dd></div>
              <div><dt>启动趋势对齐</dt><dd>{runtime.strategy_config.startup_alignment ? '启用' : '--'}</dd></div>
              <div><dt>永续反向模式</dt><dd>{runtime.allow_short ? '先平仓，再确认' : '仅做多，不开空'}</dd></div>
              <div><dt>仓位比例</dt><dd>{(runtime.position_fraction * 100).toFixed(0)}%</dd></div>
              <div><dt>杠杆 / 模式</dt><dd>{runtime.paper_model === 'futures' ? `${runtime.leverage}x ${runtime.margin_mode} · ${number(runtime.target_exposure, 2)}x 目标暴露` : '1x SPOT'}</dd></div>
              <div><dt>Taker 手续费</dt><dd>{number(runtime.fee_bps, 2)} bps</dd></div>
              <div><dt>模拟滑点</dt><dd>{number(runtime.slippage_bps, 2)} bps</dd></div>
            </dl>
          )}
          {runtime.paper_model === 'portfolio' ? (
            <dl className="strategy-values">
              <div><dt>策略版本</dt><dd>{runtime.strategy_config.algorithm_version}</dd></div>
              <div><dt>信号周期</dt><dd>UTC 日线收盘</dd></div>
              <div><dt>成交时点</dt><dd>下一 UTC 日开盘</dd></div>
              <div><dt>组合权重</dt><dd>状态 50% / 趋势 50%</dd></div>
              <div><dt>趋势袖套</dt><dd>月度固定 Top 3</dd></div>
              <div><dt>外层杠杆</dt><dd>4.0x（研究模型）</dd></div>
              <div><dt>月度锁</dt><dd>-20% / +18%</dd></div>
              <div><dt>压力账本</dt><dd>并行持久化</dd></div>
              <div><dt>指标缺失</dt><dd>状态敞口 1.0x</dd></div>
              <div><dt>前向起点</dt><dd>2026-08-16 UTC</dd></div>
            </dl>
          ) : (
          <dl className="strategy-values">
            <div><dt>实时 ATR</dt><dd>{number(strategy.atr, 4)}</dd></div>
            <div><dt>ATR 止损线</dt><dd>{money(strategy.trailing_stop, account.currency)}</dd></div>
            {runtime.paper_model === 'futures' && <div><dt>利润保护线</dt><dd>{strategy.profit_protection_active ? money(strategy.profit_stop ?? null, account.currency) : '未激活'}</dd></div>}
            <div><dt>价格距离</dt><dd>{strategy.price && strategy.trailing_stop ? money(Number(strategy.price) - Number(strategy.trailing_stop), account.currency) : '--'}</dd></div>
            <div><dt>K 线开始</dt><dd>{time(strategy.bar_start_ms)}</dd></div>
            <div><dt>K 线状态</dt><dd>形成中，Tick 实时交易</dd></div>
            <div><dt>信号检测</dt><dd>每个成交 Tick</dd></div>
            <div><dt>成交时点</dt><dd>{runtime.strategy_config.fill_timing === 'binance_actual' ? 'Binance 实际成交' : '下一成交 Tick'}</dd></div>
            <div><dt>趋势效率比</dt><dd>{number(strategy.trend_efficiency, 3)}</dd></div>
            <div><dt>趋势过滤</dt><dd>{strategy.trend_filter_passed ? 'PASS' : 'BLOCKED'}</dd></div>
            <div><dt>本 K 线动作锁</dt><dd>{strategy.action_this_bar ? 'LOCKED' : 'OPEN'}</dd></div>
            <div><dt>反向确认</dt><dd>{strategy.reversal_direction ? `等待 ${strategy.reversal_direction}` : 'NONE'}</dd></div>
          </dl>
          )}
          {runtime.paper_model === 'futures' && (
            <dl className="strategy-values futures-values">
              <div><dt>标记价格</dt><dd>{money(account.mark_price, account.currency)}</dd></div>
              <div><dt>指数价格</dt><dd>{money(account.index_price, account.currency)}</dd></div>
              <div><dt>当前资金费</dt><dd>{account.funding_rate === null ? '--' : percent(Number(account.funding_rate))}</dd></div>
              <div><dt>下次资金费</dt><dd>{time(runtime.market_state.next_funding_time_ms ?? null)}</dd></div>
              <div><dt>初始保证金</dt><dd>{money(account.initial_margin, account.currency)}</dd></div>
              <div><dt>可用余额</dt><dd>{money(account.available_balance, account.currency)}</dd></div>
              <div><dt>累计资金费</dt><dd className={Number(account.total_funding) >= 0 ? 'good-text' : 'bad-text'}>{money(account.total_funding, account.currency)}</dd></div>
            </dl>
          )}
        </div>
      </section>

      {runtime.paper_model !== 'portfolio' && <OfficialKlinePanel
        validation={account.runtime.kline_state.validation}
        intervalMinutes={account.runtime.strategy_config.bar_minutes}
        bars={bars}
        fills={accountFills}
        paperModel={account.runtime.paper_model}
        loadOlder={loadOlderOhlcv}
        loadingOlder={loadingOlderOhlcv}
        hasOlder={hasOlderOhlcv}
      />}

      <section className="lower-grid">
        <div className="panel fills-panel">
          <div className="panel-head">
            <div><span>ROUND TRIPS</span><h3>最近成交</h3></div>
            <a className="export" href={fillsExportUrl}><ArrowDownToLine size={15} />成交 CSV</a>
          </div>
          <CompletedTradeTable rows={[...completedTrades].reverse().slice(0, 8)} currency={account.currency} />
        </div>
        <div className="panel compact-equity-panel">
          <div className="panel-head"><div><span>ACCOUNT EQUITY · 30D</span><h3>最近 30 天账户净值</h3></div><strong>{money(account.equity, account.currency)}</strong></div>
          <div className="compact-chart-wrap">
            {equityChart.length > 1 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={equityChart} margin={{ top: 12, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
                  <XAxis dataKey="timestamp" hide />
                  <YAxis domain={['auto', 'auto']} hide />
                  <Tooltip labelFormatter={(value) => returnDate(Number(value))} formatter={(value) => money(Number(value), account.currency)} />
                  <Line dataKey="equity" type="monotone" stroke={CHART_COLORS.profit} strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : <div className="empty-chart">等待最近 30 天净值数据</div>}
          </div>
        </div>
      </section>
    </>
  )
}

const PriceSignalPanel = memo(function PriceSignalPanel({
  account,
  equity,
  fills,
  loadOlder,
  loadingOlder,
  hasOlder,
}: {
  account: Account
  equity: EquityPoint[]
  fills: Fill[]
  loadOlder: () => Promise<void>
  loadingOlder: boolean
  hasOlder: boolean
}) {
  const [panelRef, panelWidth] = useElementWidth<HTMLDivElement>()
  const rawPriceChart = useMemo(() => buildPriceChart(equity), [equity])
  const priceChart = useMemo(
    () => downsamplePricePoints(rawPriceChart, Math.max(160, Math.floor(panelWidth * 1.25))),
    [panelWidth, rawPriceChart],
  )
  const tradeMarkers = useMemo(
    () => buildTradeMarkers(fills, account.runtime.paper_model),
    [fills, account.runtime.paper_model],
  )
  const [priceRange, setPriceRange] = useChartRange(priceChart.length)
  const [hoveredTradeMarker, setHoveredTradeMarker] = useState<TradePoint | null>(null)
  useEffect(() => setHoveredTradeMarker(null), [account.id])
  const visibleStart = priceChart[priceRange.startIndex]
  const visibleEnd = priceChart[priceRange.endIndex]
  const visibleTradeMarkers = useMemo(
    () => tradeMarkers.filter((point) => (
      visibleStart && visibleEnd
        ? point.timestamp >= visibleStart.timestamp && point.timestamp <= visibleEnd.timestamp
        : true
    )),
    [tradeMarkers, visibleEnd, visibleStart],
  )
  const periodReturn = visibleStart && visibleEnd && visibleStart.price
    ? visibleEnd.price / visibleStart.price - 1
    : 0
  const positionQuantity = Number(account.quantity)
  const positionSide = positionQuantity > 0 ? '多头' : positionQuantity < 0 ? '空头' : '空仓'
  const averagePrice = Number(account.average_price)
  const currentPrice = account.last_price === null ? Number.NaN : Number(account.last_price)
  const trailingStopValue = account.runtime.strategy.trailing_stop
  const trailingStop = trailingStopValue === null ? Number.NaN : Number(trailingStopValue)
  const stopEstimate = positionQuantity !== 0
    && averagePrice > 0
    && Number.isFinite(trailingStop)
    ? {
        pnl: positionQuantity * (trailingStop - averagePrice),
        returnPercent: Math.sign(positionQuantity) * (trailingStop / averagePrice - 1),
      }
    : null
  const currentExitEstimate = positionQuantity !== 0
    && averagePrice > 0
    && Number.isFinite(currentPrice)
    ? {
        pnl: positionQuantity * (currentPrice - averagePrice),
        returnPercent: Math.sign(positionQuantity) * (currentPrice / averagePrice - 1),
      }
    : null
  const onBrushChange = useRafRangeChange(setPriceRange, priceChart.length)

  return (
    <div className="panel price-panel" ref={panelRef}>
      <div className="panel-head price-head">
        <div><span>PRICE / ATR TRAILING STOP</span><h3>价格与交易信号</h3></div>
        <div className="chart-summary">
          <div className="chart-controls" aria-label="价格图时间窗口">
            <button type="button" onClick={() => setPriceRange((current) => panRange(current, priceChart.length, -1))} title="向左滚动" aria-label="向左滚动价格图"><ChevronLeft size={15} /></button>
            <button type="button" onClick={() => setPriceRange((current) => panRange(current, priceChart.length, 1))} title="向右滚动" aria-label="向右滚动价格图"><ChevronRight size={15} /></button>
            <button type="button" onClick={() => setPriceRange((current) => zoomRange(current, priceChart.length, 0.65))} title="放大" aria-label="放大价格图"><ZoomIn size={15} /></button>
            <button type="button" onClick={() => setPriceRange((current) => zoomRange(current, priceChart.length, 1.55))} title="缩小" aria-label="缩小价格图"><ZoomOut size={15} /></button>
            <button type="button" onClick={() => setPriceRange(fullRange(priceChart.length))} title="显示全部" aria-label="显示全部价格数据"><Maximize2 size={14} /></button>
            <button type="button" onClick={() => void loadOlder()} disabled={!hasOlder || loadingOlder} title={hasOlder ? '加载更早价格数据' : '已加载全部价格数据'} aria-label="加载更早价格数据"><History className={loadingOlder ? 'spin' : ''} size={14} /></button>
          </div>
          <div className="chart-legend">
            <span><i className="price-dot" />价格</span>
            <span><i className="atr-dot" />ATR 止损线</span>
          </div>
          <strong className={periodReturn >= 0 ? 'good-text' : 'bad-text'}>{percent(periodReturn)}</strong>
        </div>
      </div>
      {stopEstimate && (
        <div
          className="position-stop-summary"
          role="status"
          aria-label="当前持仓 ATR 平仓预估"
          title="按当前 ATR 止损线估算，未计下一 Tick 滑点、平仓手续费和资金费"
        >
          <span className={`position-side ${positionQuantity > 0 ? 'long' : 'short'}`}>
            {positionSide}
          </span>
          <div><small>当前成本价</small><strong>{money(averagePrice, account.currency)}</strong></div>
          <div><small>ATR 平仓价</small><strong>{money(trailingStop, account.currency)}</strong></div>
          {currentExitEstimate && (
            <div>
              <small>当前价平仓收益</small>
              <strong className={currentExitEstimate.pnl >= 0 ? 'good-text' : 'bad-text'}>
                {money(currentExitEstimate.pnl, account.currency)} · {percent(currentExitEstimate.returnPercent)}
              </strong>
            </div>
          )}
          <div>
            <small>保底收益</small>
            <strong className={stopEstimate.pnl >= 0 ? 'good-text' : 'bad-text'}>
              {money(stopEstimate.pnl, account.currency)} · {percent(stopEstimate.returnPercent)}
            </strong>
          </div>
        </div>
      )}
      <PriceChartCanvas
        currency={account.currency}
        points={priceChart}
        range={priceRange}
        tradeMarkers={tradeMarkers}
        visibleTradeMarkers={visibleTradeMarkers}
        hoveredTradeMarker={hoveredTradeMarker}
        setHoveredTradeMarker={setHoveredTradeMarker}
        onBrushChange={onBrushChange}
      />
    </div>
  )
})

const PriceChartCanvas = memo(function PriceChartCanvas({
  currency,
  points,
  range,
  tradeMarkers,
  visibleTradeMarkers,
  hoveredTradeMarker,
  setHoveredTradeMarker,
  onBrushChange,
}: {
  currency: string
  points: PricePoint[]
  range: ChartRange
  tradeMarkers: TradePoint[]
  visibleTradeMarkers: TradePoint[]
  hoveredTradeMarker: TradePoint | null
  setHoveredTradeMarker: (trade: TradePoint | null) => void
  onBrushChange: (next: { startIndex?: number; endIndex?: number }) => void
}) {
  const visibleStart = points[range.startIndex]
  return (
    <div className="price-chart-wrap">
      {points.length > 1 ? (
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 24, right: 24, bottom: 2, left: 2 }}>
            <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
            <XAxis dataKey="timestamp" type="number" domain={['dataMin', 'dataMax']} stroke={CHART_COLORS.axis} tickLine={false} axisLine={false} minTickGap={70} fontSize={11} tickFormatter={(value) => time(Number(value))} />
            <YAxis domain={['auto', 'auto']} stroke={CHART_COLORS.axis} tickLine={false} axisLine={false} width={58} fontSize={11} tickFormatter={(value) => Number(value).toFixed(2)} />
            <Tooltip content={<PriceTooltip currency={currency} pricePoints={points} tradePoints={tradeMarkers} hoveredTrade={hoveredTradeMarker} referencePrice={visibleStart?.price ?? null} />} />
            <Line dataKey="price" name="价格" type="monotone" stroke={CHART_COLORS.price} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="trailingStop" name="ATR 止损线" type="stepAfter" stroke={CHART_COLORS.atr} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
            {visibleTradeMarkers.map((point) => (
              <ReferenceDot
                key={point.id}
                x={point.timestamp}
                y={point.price}
                r={10}
                zIndex={0}
                shape={(props) => (
                  <TradeMarker
                    cx={props.cx}
                    cy={props.cy}
                    payload={point}
                    onHover={setHoveredTradeMarker}
                  />
                )}
              />
            ))}
            <Brush dataKey="timestamp" startIndex={range.startIndex} endIndex={range.endIndex} height={25} travellerWidth={9} stroke={CHART_COLORS.brushStroke} fill={CHART_COLORS.brush} tickFormatter={(value) => time(Number(value))} onChange={onBrushChange} />
          </ComposedChart>
        </ResponsiveContainer>
      ) : <div className="empty-chart">正在积累价格与 ATR 快照</div>}
    </div>
  )
})

function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(800)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      setWidth(Math.max(1, Math.round(entry.contentRect.width)))
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return [ref, width] as const
}

function useRafRangeChange(
  setRange: React.Dispatch<React.SetStateAction<ChartRange>>,
  pointCount: number,
) {
  const frame = useRef<number | null>(null)
  const pending = useRef<ChartRange | null>(null)

  useEffect(() => () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current)
  }, [])

  return useCallback((next: { startIndex?: number; endIndex?: number }) => {
    if (typeof next.startIndex !== 'number' || typeof next.endIndex !== 'number') return
    pending.current = clampRange(
      { startIndex: next.startIndex, endIndex: next.endIndex },
      pointCount,
    )
    if (frame.current !== null) return
    frame.current = requestAnimationFrame(() => {
      frame.current = null
      if (pending.current) setRange(pending.current)
    })
  }, [pointCount, setRange])
}

function useChartRange(pointCount: number, defaultVisiblePoints = DEFAULT_VISIBLE_POINTS) {
  const [range, setRange] = useState<ChartRange>(() => (
    latestRange(pointCount, defaultVisiblePoints)
  ))
  const previousCount = useRef(pointCount)

  useEffect(() => {
    const previous = previousCount.current
    previousCount.current = pointCount
    setRange((current) => {
      if (pointCount <= 0) return fullRange(pointCount)
      if (previous <= 0) return latestRange(pointCount, defaultVisiblePoints)
      const followedLatest = current.endIndex >= previous - 2
      if (!followedLatest) return clampRange(current, pointCount)
      const currentSize = current.endIndex - current.startIndex + 1
      const nextSize = currentSize >= previous ? pointCount : currentSize
      return rangeEndingAt(pointCount - 1, nextSize, pointCount)
    })
  }, [defaultVisiblePoints, pointCount])

  return [range, setRange] as const
}

function fullRange(pointCount: number): ChartRange {
  return { startIndex: 0, endIndex: Math.max(0, pointCount - 1) }
}

function latestRange(pointCount: number, defaultVisiblePoints = DEFAULT_VISIBLE_POINTS): ChartRange {
  if (pointCount <= 0) return fullRange(pointCount)
  return rangeEndingAt(
    pointCount - 1,
    Math.min(defaultVisiblePoints, pointCount),
    pointCount,
  )
}

function clampRange(range: ChartRange, pointCount: number): ChartRange {
  if (pointCount <= 0) return fullRange(pointCount)
  const startIndex = Math.max(0, Math.min(Math.round(range.startIndex), pointCount - 1))
  const endIndex = Math.max(startIndex, Math.min(Math.round(range.endIndex), pointCount - 1))
  return { startIndex, endIndex }
}

function rangeEndingAt(endIndex: number, size: number, pointCount: number): ChartRange {
  if (pointCount <= 0) return fullRange(pointCount)
  const boundedSize = Math.max(1, Math.min(Math.round(size), pointCount))
  const boundedEnd = Math.max(boundedSize - 1, Math.min(endIndex, pointCount - 1))
  return {
    startIndex: boundedEnd - boundedSize + 1,
    endIndex: boundedEnd,
  }
}

function panRange(current: ChartRange, pointCount: number, direction: -1 | 1): ChartRange {
  if (pointCount <= 1) return fullRange(pointCount)
  const size = current.endIndex - current.startIndex + 1
  const distance = Math.max(1, Math.round(size * 0.35)) * direction
  const nextStart = Math.max(0, Math.min(current.startIndex + distance, pointCount - size))
  return { startIndex: nextStart, endIndex: nextStart + size - 1 }
}

function zoomRange(current: ChartRange, pointCount: number, factor: number): ChartRange {
  if (pointCount <= 1) return fullRange(pointCount)
  const currentSize = current.endIndex - current.startIndex + 1
  const minimum = Math.min(MIN_VISIBLE_POINTS, pointCount)
  const nextSize = Math.max(minimum, Math.min(pointCount, Math.round(currentSize * factor)))
  const center = (current.startIndex + current.endIndex) / 2
  const nextStart = Math.max(0, Math.min(Math.round(center - (nextSize - 1) / 2), pointCount - nextSize))
  return { startIndex: nextStart, endIndex: nextStart + nextSize - 1 }
}

function buildPriceChart(equity: EquityPoint[]): PricePoint[] {
  const valid = equity.filter((point) => Number.isFinite(Number(point.price)))
  return valid.map((point) => ({
    timestamp: point.timestamp_ms,
    price: Number(point.price),
    trailingStop: point.trailing_stop === null ? null : Number(point.trailing_stop),
  }))
}

function downsamplePricePoints(points: PricePoint[], maxPoints: number) {
  if (points.length <= maxPoints || maxPoints < 6) return points
  const result: PricePoint[] = [points[0]]
  const interiorCount = points.length - 2
  const bucketCount = Math.max(1, Math.floor((maxPoints - 2) / 4))

  for (let bucket = 0; bucket < bucketCount; bucket += 1) {
    const start = 1 + Math.floor(bucket * interiorCount / bucketCount)
    const end = 1 + Math.floor((bucket + 1) * interiorCount / bucketCount)
    let minimumPrice = points[start]
    let maximumPrice = points[start]
    let minimumStop: PricePoint | null = points[start].trailingStop === null ? null : points[start]
    let maximumStop = minimumStop
    for (let index = start + 1; index < end; index += 1) {
      const point = points[index]
      if (point.price < minimumPrice.price) minimumPrice = point
      if (point.price > maximumPrice.price) maximumPrice = point
      if (point.trailingStop !== null) {
        if (minimumStop === null || point.trailingStop < minimumStop.trailingStop!) minimumStop = point
        if (maximumStop === null || point.trailingStop > maximumStop.trailingStop!) maximumStop = point
      }
    }
    const candidates = new Set([minimumPrice, maximumPrice, minimumStop, maximumStop])
    result.push(...[...candidates].filter((point): point is PricePoint => point !== null).sort(
      (left, right) => left.timestamp - right.timestamp,
    ))
  }

  result.push(points[points.length - 1])
  return result
}

type KlinePoint = {
  timestamp: number
  endTimestamp: number
  open: number
  high: number
  low: number
  close: number
  isClosed: boolean
  source: string
}

function buildKlineChart(bars: OhlcvBar[]): KlinePoint[] {
  return [...bars]
    .sort((left, right) => left.start_ms - right.start_ms)
    .map((bar) => ({
      timestamp: bar.start_ms,
      endTimestamp: bar.end_ms,
      open: Number(bar.open),
      high: Number(bar.high),
      low: Number(bar.low),
      close: Number(bar.close),
      isClosed: bar.is_closed,
      source: bar.source,
    }))
}

const OfficialKlinePanel = memo(function OfficialKlinePanel({
  validation,
  intervalMinutes,
  bars,
  fills,
  paperModel,
  loadOlder,
  loadingOlder,
  hasOlder,
}: {
  validation: string
  intervalMinutes: number
  bars: OhlcvBar[]
  fills: Fill[]
  paperModel: 'spot' | 'futures' | 'portfolio'
  loadOlder: () => Promise<void>
  loadingOlder: boolean
  hasOlder: boolean
}) {
  const intervalLabel = intervalMinutes === 1440 ? '日线' : `${intervalMinutes} 分钟`
  const klineChart = useMemo(() => buildKlineChart(bars), [bars])
  const tradeMarkers = useMemo(
    () => buildTradeMarkers(fills, paperModel),
    [fills, paperModel],
  )
  const [range, setRange] = useChartRange(klineChart.length, DEFAULT_VISIBLE_KLINES)
  const visibleBars = klineChart.slice(range.startIndex, range.endIndex + 1)
  const visibleStart = visibleBars[0]
  const visibleEnd = visibleBars[visibleBars.length - 1]
  const visibleTradeMarkers = useMemo(
    () => tradeMarkers.filter((trade) => (
      visibleStart && visibleEnd
        ? trade.timestamp >= visibleStart.timestamp && trade.timestamp <= visibleEnd.endTimestamp
        : true
    )),
    [tradeMarkers, visibleEnd, visibleStart],
  )
  const pan = (direction: -1 | 1) => {
    setRange((current) => panRange(current, klineChart.length, direction))
  }
  const zoom = (factor: number) => {
    setRange((current) => zoomRange(current, klineChart.length, factor))
  }

  return (
    <section className="panel kline-panel" data-testid="official-kline-panel">
      <div className="panel-head price-head">
        <div><span>BINANCE OFFICIAL OHLCV</span><h3>官方 {intervalLabel} K线</h3></div>
        <div className="chart-summary">
          <div className="chart-controls" aria-label="K线时间窗口">
            <button type="button" onClick={() => pan(-1)} title="向左滚动K线" aria-label="向左滚动K线"><ChevronLeft size={15} /></button>
            <button type="button" onClick={() => pan(1)} title="向右滚动K线" aria-label="向右滚动K线"><ChevronRight size={15} /></button>
            <button type="button" onClick={() => zoom(0.65)} title="放大K线" aria-label="放大K线"><ZoomIn size={15} /></button>
            <button type="button" onClick={() => zoom(1.55)} title="缩小K线" aria-label="缩小K线"><ZoomOut size={15} /></button>
            <button type="button" onClick={() => setRange(fullRange(klineChart.length))} title="显示全部K线" aria-label="显示全部K线"><Maximize2 size={14} /></button>
            <button type="button" onClick={() => void loadOlder()} disabled={!hasOlder || loadingOlder} title={hasOlder ? '加载更早K线' : '已加载全部K线'} aria-label="加载更早K线"><History className={loadingOlder ? 'spin' : ''} size={14} /></button>
          </div>
          <div className="chart-legend kline-legend">
            <span><i className="candle-up" />上涨</span>
            <span><i className="candle-down" />下跌</span>
            <span className={`kline-validation ${validation.toLowerCase()}`}>{validation}</span>
          </div>
        </div>
      </div>
      <div className="kline-chart-wrap">
        {visibleBars.length > 0 ? (
          <KlineSvg bars={visibleBars} trades={visibleTradeMarkers} />
        ) : <div className="empty-chart">等待官方 {intervalLabel} K线</div>}
      </div>
    </section>
  )
})

const KlineSvg = memo(function KlineSvg({ bars, trades }: { bars: KlinePoint[]; trades: TradePoint[] }) {
  const width = 1200
  const height = 360
  const left = 58
  const right = 18
  const top = 18
  const bottom = 32
  const chartWidth = width - left - right
  const chartHeight = height - top - bottom
  const minimum = Math.min(...bars.map((bar) => bar.low))
  const maximum = Math.max(...bars.map((bar) => bar.high))
  const padding = Math.max((maximum - minimum) * 0.08, 0.01)
  const domainLow = minimum - padding
  const domainHigh = maximum + padding
  const y = (value: number) => top + (domainHigh - value) / (domainHigh - domainLow) * chartHeight
  const x = (index: number) => left + (bars.length <= 1 ? chartWidth / 2 : index / (bars.length - 1) * chartWidth)
  const candleWidth = Math.max(3, Math.min(14, chartWidth / Math.max(bars.length, 1) * 0.62))
  const tradeMarkers = trades.flatMap((trade) => {
    const index = bars.findIndex((bar) => trade.timestamp >= bar.timestamp && trade.timestamp <= bar.endTimestamp)
    if (index < 0) return []
    return [{ trade, index }]
  })
  const gridValues = [0, 0.25, 0.5, 0.75, 1].map((ratio) => domainHigh - ratio * (domainHigh - domainLow))

  return (
    <svg className="kline-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Binance官方15分钟K线图" data-testid="official-kline-chart">
      {gridValues.map((value) => (
        <g key={value}>
          <line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="kline-grid" />
          <text x={left - 8} y={y(value) + 4} textAnchor="end" className="kline-axis-label">{value.toFixed(2)}</text>
        </g>
      ))}
      {bars.map((bar, index) => {
        const center = x(index)
        const up = bar.close >= bar.open
        const color = up ? CHART_COLORS.profit : CHART_COLORS.loss
        const bodyTop = y(Math.max(bar.open, bar.close))
        const bodyHeight = Math.max(2, Math.abs(y(bar.open) - y(bar.close)))
        return (
          <g key={bar.timestamp} className="kline-candle">
            <title>{`${time(bar.timestamp)} O ${bar.open.toFixed(4)} H ${bar.high.toFixed(4)} L ${bar.low.toFixed(4)} C ${bar.close.toFixed(4)}${bar.isClosed ? '' : ' · 形成中'}`}</title>
            <line x1={center} x2={center} y1={y(bar.high)} y2={y(bar.low)} stroke={color} strokeWidth={1.5} />
            <rect x={center - candleWidth / 2} y={bodyTop} width={candleWidth} height={bodyHeight} fill={color} fillOpacity={bar.isClosed ? 0.88 : 0.45} />
          </g>
        )
      })}
      {tradeMarkers.map(({ trade, index }) => {
        const center = x(index)
        const markerY = y(trade.price)
        const isLong = trade.action === 'LONG'
        const isShort = trade.action === 'SHORT'
        const Icon = isLong ? CircleArrowUp : isShort ? CircleArrowDown : CircleX
        const color = isLong ? CHART_COLORS.profit : isShort ? CHART_COLORS.loss : CHART_COLORS.close
        const labelY = isLong ? markerY + 24 : markerY - 14
        return (
          <g key={trade.id} className="kline-trade-marker" data-testid={`kline-marker-${trade.action.toLowerCase()}`}>
            <Icon x={center - 8} y={markerY - 8} width={16} height={16} color={color} fill={CHART_COLORS.canvas} strokeWidth={2.2} />
            <text x={center} y={labelY} textAnchor="middle" fill={color} className="kline-trade-label">
              {trade.action}
            </text>
          </g>
        )
      })}
      {bars.length > 0 && <text x={left} y={height - 8} className="kline-axis-label">{time(bars[0].timestamp)}</text>}
      {bars.length > 1 && <text x={width - right} y={height - 8} textAnchor="end" className="kline-axis-label">{time(bars[bars.length - 1].timestamp)}</text>}
    </svg>
  )
})

function buildTradeMarkers(fills: Fill[], paperModel: 'spot' | 'futures' | 'portfolio'): TradePoint[] {
  const ordered = [...fills].sort((left, right) => (
    left.timestamp_ms - right.timestamp_ms
    || (left.position_effect === 'CLOSE' ? -1 : right.position_effect === 'CLOSE' ? 1 : 0)
  ))
  let inferredPosition: 'LONG' | 'SHORT' | 'FLAT' = 'FLAT'

  return ordered.map((fill) => {
    const side = fill.side as 'BUY' | 'SELL'
    const beforeValue = Number(fill.position_before)
    const afterValue = Number(fill.position_after)
    const before = fill.position_before !== null && Number.isFinite(beforeValue) ? beforeValue : null
    const after = fill.position_after !== null && Number.isFinite(afterValue) ? afterValue : null
    let action: TradePoint['action']

    if (
      fill.position_effect === 'CLOSE'
      || (before !== null && after === 0 && before !== 0)
    ) {
      action = 'CLOSE'
    } else if (fill.position_effect === 'OPEN') {
      action = after !== null ? (after < 0 ? 'SHORT' : 'LONG') : (side === 'SELL' ? 'SHORT' : 'LONG')
    } else if (before !== null && after !== null && before === 0 && after !== 0) {
      action = after < 0 ? 'SHORT' : 'LONG'
    } else if (paperModel === 'spot') {
      action = side === 'BUY' ? 'LONG' : 'CLOSE'
    } else {
      action = side === 'BUY'
        ? (inferredPosition === 'SHORT' ? 'CLOSE' : 'LONG')
        : (inferredPosition === 'LONG' ? 'CLOSE' : 'SHORT')
    }

    if (after !== null) {
      inferredPosition = after > 0 ? 'LONG' : after < 0 ? 'SHORT' : 'FLAT'
    } else {
      inferredPosition = action === 'LONG' ? 'LONG' : action === 'SHORT' ? 'SHORT' : 'FLAT'
    }

    return {
      id: fill.id,
      timestamp: fill.timestamp_ms,
      price: Number(fill.price),
      side,
      action,
    }
  })
}

function buildCompletedTrades(fills: Fill[], funding: FundingPayment[] = []): CompletedTrade[] {
  const ordered = [...fills].sort((left, right) => (
    left.timestamp_ms - right.timestamp_ms
    || (left.position_effect === 'CLOSE' ? -1 : right.position_effect === 'CLOSE' ? 1 : 0)
  ))
  const trades: CompletedTrade[] = []
  let entry: Fill | null = null

  for (const fill of ordered) {
    const quantity = Number(fill.quantity)
    if (fill.position_effect === 'OPEN' && quantity > 0) {
      entry = fill
      continue
    }
    if (fill.position_effect === 'CLOSE') {
      if (quantity > 0 && entry) {
        const fundingPnl = funding
          .filter((payment) => payment.timestamp_ms >= entry!.timestamp_ms && payment.timestamp_ms <= fill.timestamp_ms)
          .reduce((total, payment) => total + Number(payment.amount), 0)
        const netPnl = Number(fill.realized_pnl ?? 0) - Number(entry.fee) + fundingPnl
        const entryNotional = Number(entry.notional)
        trades.push({
          direction: entry.side === 'SELL' ? 'SHORT' : 'LONG',
          entryTimestamp: entry.timestamp_ms,
          exitTimestamp: fill.timestamp_ms,
          entryPrice: Number(entry.price),
          exitPrice: Number(fill.price),
          quantity,
          fees: Number(entry.fee) + Number(fill.fee),
          fundingPnl,
          netPnl,
          returnPercent: entryNotional ? netPnl / entryNotional : 0,
        })
        entry = null
      }
      continue
    }

    if (fill.side === 'BUY' && quantity > 0) {
      entry = fill
      continue
    }
    if (fill.side !== 'SELL' || quantity <= 0 || !entry) continue

    const entryQuantity = Number(entry.quantity)
    if (entryQuantity <= 0) continue
    const matchedQuantity = Math.min(entryQuantity, quantity)
    const entryUnitCost = (Number(entry.notional) + Number(entry.fee)) / entryQuantity
    const exitUnitProceeds = (Number(fill.notional) - Number(fill.fee)) / quantity
    const fees = (
      Number(entry.fee) / entryQuantity
      + Number(fill.fee) / quantity
    ) * matchedQuantity
    const fundingPnl = funding
      .filter((payment) => payment.timestamp_ms >= entry!.timestamp_ms && payment.timestamp_ms <= fill.timestamp_ms)
      .reduce((total, payment) => total + Number(payment.amount), 0)
    const netPnl = (exitUnitProceeds - entryUnitCost) * matchedQuantity + fundingPnl
    trades.push({
      direction: 'LONG',
      entryTimestamp: entry.timestamp_ms,
      exitTimestamp: fill.timestamp_ms,
      entryPrice: Number(entry.price),
      exitPrice: Number(fill.price),
      quantity: matchedQuantity,
      fees,
      fundingPnl,
      netPnl,
      returnPercent: netPnl / (entryUnitCost * matchedQuantity),
    })
    entry = null
  }

  return trades
}

type TradeMarkerProps = {
  cx?: number
  cy?: number
  payload?: TradePoint
  onHover?: (trade: TradePoint | null) => void
}

function TradeMarker({ cx = 0, cy = 0, payload, onHover }: TradeMarkerProps) {
  if (!payload || (payload.side !== 'BUY' && payload.side !== 'SELL')) return null
  const isLong = payload.action === 'LONG'
  const isShort = payload.action === 'SHORT'
  const Icon = isLong ? CircleArrowUp : isShort ? CircleArrowDown : CircleX
  const color = isLong ? CHART_COLORS.profit : isShort ? CHART_COLORS.loss : CHART_COLORS.close
  const label = payload.action
  const labelY = isLong ? cy + 30 : cy - 21
  return (
    <g
      data-testid={`trade-marker-${payload.action.toLowerCase()}`}
      onMouseEnter={() => onHover?.(payload)}
      onMouseLeave={() => onHover?.(null)}
    >
      <Icon x={cx - 10} y={cy - 10} width={20} height={20} color={color} fill={CHART_COLORS.canvas} strokeWidth={2.2} />
      <text x={cx} y={labelY} textAnchor="middle" fill={color} className="trade-marker-label">{label}</text>
    </g>
  )
}

function PriceTooltip({
  active,
  payload,
  label,
  currency,
  pricePoints,
  tradePoints,
  hoveredTrade,
  referencePrice,
}: {
  active?: boolean
  payload?: Array<{ payload?: PricePoint | TradePoint }>
  label?: number | string
  currency: string
  pricePoints: PricePoint[]
  tradePoints: TradePoint[]
  hoveredTrade: TradePoint | null
  referencePrice: number | null
}) {
  const values = (payload ?? []).flatMap((item) => item.payload ? [item.payload] : [])
  const payloadPrice = values.find(isPricePoint)
  const timestamp = Number(label ?? payloadPrice?.timestamp)
  if (!active || !Number.isFinite(timestamp)) return null

  const point = pricePoints.find((item) => item.timestamp === timestamp)
    ?? nearestPricePoint(pricePoints, timestamp)
    ?? payloadPrice
  const trade = hoveredTrade ?? nearestTradeForPricePoint(tradePoints, pricePoints, timestamp)
  if (!point && !trade) return null
  const change = point && referencePrice ? point.price / referencePrice - 1 : null

  return (
    <div className="price-tooltip">
      <time>{time(timestamp)}</time>
      {point && <div><span>市场价格</span><strong>{money(point.price, currency)}</strong></div>}
      {point && <div><span>ATR 止损线</span><strong>{money(point.trailingStop, currency)}</strong></div>}
      {change !== null && (
        <div><span>可视区间涨跌</span><strong className={change >= 0 ? 'good-text' : 'bad-text'}>{percent(change)}</strong></div>
      )}
      {trade && (
        <div>
          <span>交易动作</span>
          <strong className={trade.action === 'LONG' ? 'good-text' : trade.action === 'SHORT' ? 'bad-text' : ''}>
            {trade.action} · {money(trade.price, currency)}
          </strong>
        </div>
      )}
    </div>
  )
}

function nearestPricePoint(points: PricePoint[], timestamp: number) {
  return points.reduce<PricePoint | undefined>((nearest, point) => (
    !nearest || Math.abs(point.timestamp - timestamp) < Math.abs(nearest.timestamp - timestamp)
      ? point
      : nearest
  ), undefined)
}

function nearestTradeForPricePoint(
  trades: TradePoint[],
  points: PricePoint[],
  timestamp: number,
) {
  const pointIndex = points.findIndex((point) => point.timestamp === timestamp)
  if (pointIndex < 0) return undefined
  const previous = points[pointIndex - 1]
  const next = points[pointIndex + 1]
  const lowerBound = previous ? (previous.timestamp + timestamp) / 2 : Number.NEGATIVE_INFINITY
  const upperBound = next ? (timestamp + next.timestamp) / 2 : Number.POSITIVE_INFINITY
  return trades.reduce<TradePoint | undefined>((nearest, trade) => {
    if (trade.timestamp < lowerBound || trade.timestamp > upperBound) return nearest
    return !nearest || Math.abs(trade.timestamp - timestamp) < Math.abs(nearest.timestamp - timestamp)
      ? trade
      : nearest
  }, undefined)
}

function isPricePoint(value: PricePoint | TradePoint): value is PricePoint {
  return 'trailingStop' in value
}

function Metric({ label, value, sub, tone = '', icon }: { label: string; value: string; sub: string; tone?: string; icon: React.ReactNode }) {
  return <div className={`metric ${tone}`}><div className="metric-top"><span>{label}</span>{icon}</div><strong>{value}</strong><small>{sub}</small></div>
}

const portfolioSleeveLabels: Record<string, string> = {
  lead_lag: 'BTC/ETH 15日冲击 Lead-Lag',
  'event-eth_perp-to-eth_perp-continuation-60d-threshold-2p5-hold-12x4h-none-long_only': 'ETH→ETH 60日延续',
  'event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short': 'BTC→BTC 15日延续',
  'event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-hold-12x4h-underreaction-long_short': 'ETH→BTC 60日低反应',
}

function portfolioSleeveLabel(id: string) {
  if (portfolioSleeveLabels[id]) return portfolioSleeveLabels[id]
  const match = id.match(/^(btc|eth)_perp-macd-1440m-(\d+)-(\d+)-(\d+)-long_only-confirm(\d+)$/)
  if (!match) return id
  return `${match[1].toUpperCase()} MACD(${match[2]},${match[3]},${match[4]}) · 确认${match[5]}日`
}

function precisePercent(value: number, digits = 3) {
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`
}

function signedExposure(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}x`
}

function aggregatePortfolioTargets(
  day: PortfolioLedgerDay,
  outerExposure = Number(day.state.outer_exposure),
) {
  const btcState = Number(day.state.state_targets.btc) * 0.5 * outerExposure
  const ethState = Number(day.state.state_targets.eth) * 0.5 * outerExposure
  return Object.values(day.state.sleeves.trend.components).reduce(
    (targets, component) => {
      const contribution = Number(component.target) * outerExposure / 6
      if (component.instrument_id === 'btc_perp') targets.btc += contribution
      else targets.eth += contribution
      return targets
    },
    { btc: btcState, eth: ethState },
  )
}

function stateSleeveContribution(component: PortfolioSleeveComponent, day: PortfolioLedgerDay) {
  const componentReturn = Number(component.return)
  const anchorReturn = Number(day.state.state_anchor_return)
  const currentAllocation = Number(component.allocated_equity ?? 0)
  const currentAnchor = Number(day.state.sleeves.state.anchor_equity)
  if (![componentReturn, anchorReturn, currentAllocation, currentAnchor].every(Number.isFinite)
      || currentAnchor === 0 || componentReturn === -1 || anchorReturn === -1) return 0
  const previousAllocation = currentAllocation / (1 + componentReturn)
  const previousAnchor = currentAnchor / (1 + anchorReturn)
  return previousAllocation * componentReturn / previousAnchor
    * Number(day.state.state_combined_exposure)
    * 0.5
    * Number(day.state.outer_exposure)
}

function trendSleeveContribution(component: PortfolioSleeveComponent, day: PortfolioLedgerDay) {
  return Number(component.return) / 6 * Number(day.state.outer_exposure)
}

function portfolioPosition(component: PortfolioSleeveComponent, shadowOnly = false) {
  const target = Number(component.target)
  const asset = component.instrument_id === 'btc_perp' ? 'BTC' : 'ETH'
  const shadow = shadowOnly ? '影子' : ''
  if (target > 0) return `${asset} ${shadow}多头`
  if (target < 0) return `${asset} ${shadow}空头`
  return `${asset} 空仓`
}

function utcCloseLabel(timestamp: number) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(timestamp).replace('24:00', '00:00')
}

function utcTimestamp(timestamp: number) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(timestamp)
}

function portfolioSleeveWeight(event: PortfolioSleeveEvent) {
  const anchorAllocation = Number(event.payload.anchor_allocation)
  if (Number.isFinite(anchorAllocation)) return anchorAllocation * 0.5
  if (/^(btc|eth)_perp-macd-/.test(event.sleeve_id)) return 1 / 6
  return null
}

function useUtcCloseCountdown() {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const date = new Date(now)
  const nextClose = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() + 1)
  const remainingSeconds = Math.max(0, Math.ceil((nextClose - now) / 1000))
  const hours = Math.floor(remainingSeconds / 3600)
  const minutes = Math.floor((remainingSeconds % 3600) / 60)
  const seconds = remainingSeconds % 60
  return {
    nextClose,
    countdown: [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':'),
  }
}

function PortfolioAttribution({
  runtime,
  ledger,
  loading,
}: {
  runtime: Account['runtime']
  ledger: PortfolioLedgerDay[]
  loading: boolean
}) {
  const { nextClose, countdown } = useUtcCloseCountdown()
  const day = ledger[ledger.length - 1]
  if (!day) {
    return (
      <section className="panel portfolio-attribution-panel" aria-label="组合持仓与收益归因">
        <div className="portfolio-empty"><Activity size={18} />{loading ? '正在读取组合袖套账本' : '等待首个完整 UTC 日账本'}</div>
      </section>
    )
  }

  const ledgerOuterExposure = Number(day.state.outer_exposure)
  const effectiveOuterExposure = Number(
    runtime.market_state.effective_outer_exposure ?? ledgerOuterExposure,
  )
  const shadowOnly = effectiveOuterExposure === 0
  const stateContribution = Number(day.state.state_return) * 0.5 * ledgerOuterExposure
  const trendContribution = Object.values(day.state.sleeves.trend.components)
    .reduce((total, component) => total + trendSleeveContribution(component, day), 0)
  const innerCosts = Number(day.state.costs.component_fee)
    + Number(day.state.costs.component_slippage)
    + Number(day.state.costs.state_route)
    + Number(day.state.costs.calendar_route)
  const modeledCostDrag = -innerCosts * ledgerOuterExposure - Number(day.state.costs.outer_route)
  const fundingContribution = Number(day.state.funding_return) * ledgerOuterExposure
  const targets = aggregatePortfolioTargets(day, effectiveOuterExposure)
  const rows = [
    ...Object.entries(day.state.sleeves.state.components).map(([id, component]) => ({
      id,
      group: '状态因子',
      internalAllocation: Number(component.allocation ?? 0),
      portfolioWeight: Number(component.allocation ?? 0) * 0.5,
      component,
      contribution: stateSleeveContribution(component, day),
    })),
    ...Object.entries(day.state.sleeves.trend.components).map(([id, component]) => ({
      id,
      group: '月历趋势',
      internalAllocation: null,
      portfolioWeight: 1 / 6,
      component,
      contribution: trendSleeveContribution(component, day),
    })),
  ]

  return (
    <section className="panel portfolio-attribution-panel" aria-label="组合持仓与收益归因">
      <div className="portfolio-attribution-head">
        <div>
          <span>PORTFOLIO ATTRIBUTION / {day.day} UTC</span>
          <h3>组合持仓与收益归因</h3>
        </div>
        <div className="utc-close-countdown" aria-label="下一个 UTC 收盘倒计时">
          <Clock3 size={16} />
          <div><span>下一个 UTC 收盘</span><strong>{countdown}</strong><small>{utcCloseLabel(nextClose)} UTC</small></div>
        </div>
      </div>

      <div className="portfolio-attribution-summary">
        <div><span>组合日收益</span><strong className={Number(day.daily_return) >= 0 ? 'good-text' : 'bad-text'}>{precisePercent(Number(day.daily_return))}</strong><small>{day.day} 应用 {number(ledgerOuterExposure, 1)}x</small></div>
        <div><span>状态袖套贡献</span><strong className={stateContribution >= 0 ? 'good-text' : 'bad-text'}>{precisePercent(stateContribution)}</strong><small>组合权重 50%</small></div>
        <div><span>趋势袖套贡献</span><strong className={trendContribution >= 0 ? 'good-text' : 'bad-text'}>{precisePercent(trendContribution)}</strong><small>3 个袖套各 1/6</small></div>
        <div><span>成本拖累</span><strong className="bad-text">{precisePercent(modeledCostDrag)}</strong><small>已包含在袖套收益中</small></div>
        <div><span>资金费贡献</span><strong className={fundingContribution >= 0 ? 'good-text' : 'bad-text'}>{precisePercent(fundingContribution)}</strong><small>已包含在袖套收益中</small></div>
        <div><span>组合建模目标</span><strong>BTC {signedExposure(targets.btc)}</strong><small>ETH {signedExposure(targets.eth)}</small></div>
      </div>

      <div className="portfolio-context-strip">
        <span>市场状态 <strong>{day.state.metrics.state.toUpperCase()}</strong></span>
        <span>z-score <strong>{number(day.state.metrics.zscore, 3)}</strong></span>
        <span>状态敞口 <strong>{number(day.state.state_metric_exposure, 2)}x</strong></span>
        <span>波动率敞口 <strong>{number(day.state.state_volatility_exposure, 2)}x</strong></span>
        <span>月度锁 <strong>{runtime.market_state.month_locked ? 'LOCKED' : 'OPEN'}</strong></span>
        <span>当前有效外层 <strong>{number(effectiveOuterExposure, 1)}x</strong></span>
        {runtime.market_state.month_locked && <span>内部袖套 <strong>SHADOW ONLY</strong></span>}
      </div>

      <div className="portfolio-sleeve-table-wrap">
        <table className="portfolio-sleeve-table">
          <thead><tr><th>袖套</th><th>组合基准权重</th><th>袖套影子持仓</th><th>目标</th><th>数量</th><th>袖套日收益</th><th>账户贡献</th><th>袖套成本</th></tr></thead>
          <tbody>
            {rows.map(({ id, group, internalAllocation, portfolioWeight, component, contribution }) => (
              <tr key={id}>
                <td><span>{group}{internalAllocation === null ? '' : ` · 状态内 ${(internalAllocation * 100).toFixed(0)}%`}</span><strong>{portfolioSleeveLabel(id)}</strong></td>
                <td><strong className="portfolio-weight">{(portfolioWeight * 100).toFixed(2)}%</strong></td>
                <td><span className={`position-pill ${Number(component.target) > 0 ? 'long' : Number(component.target) < 0 ? 'short' : 'flat'}`}>{portfolioPosition(component, shadowOnly)}</span></td>
                <td>{signedExposure(Number(component.target))}</td>
                <td>{number(Math.abs(Number(component.quantity)), 4)}</td>
                <td className={Number(component.return) >= 0 ? 'good-text' : 'bad-text'}>{precisePercent(Number(component.return))}</td>
                <td className={contribution >= 0 ? 'good-text' : 'bad-text'}>{precisePercent(contribution)}</td>
                <td><small>费 {money(component.fee_amount, 'USDT')} · 滑 {money(component.slippage_amount, 'USDT')} · 资金 {money(component.funding_amount, 'USDT')}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="portfolio-ledger-note">收益归因来自最近一个已完成 UTC 日的不可变账本。袖套数量用于连续影子信号计算；组合当前风险只由“当前有效外层”决定，月度锁定时为 0x。</p>
    </section>
  )
}

const decisionText = {
  PAUSED: ['策略已暂停', '行情和 ATR 继续更新，但不会发出交易信号'],
  WARMING_UP: ['ATR 预热中', '历史 K 线不足，暂时不能判断穿越'],
  ORDER_PENDING: ['订单待成交', '信号已提交，正在等待下一个 Tick 模拟撮合'],
  HOLDING_LONG: ['持仓监控中', '已持有多头，等待价格实时向下穿越 ATR 止损线'],
  HOLDING_SHORT: ['空头监控中', '已持有空头，等待价格实时向上穿越 ATR 止损线'],
  ACTION_LOCKED: ['本 K 线动作锁定', '本根 15 分钟 K 线已经执行一次交易，下一根 K 线前不再交易'],
  REVERSAL_CONFIRMATION: ['等待反向确认', '已平仓，只有下一根 K 线继续突破确认距离才开反向仓位'],
  TREND_FILTERED: ['震荡过滤中', '当前趋势效率不足，不建立新仓位'],
  ARMED_FOR_BUY: ['等待向上穿越', '当前价格在 ATR 线下方，下一次实时向上穿越可触发买入'],
  ARMED_FOR_LONG: ['等待做多', '当前价格在 ATR 线下方，向上穿越后建立多头'],
  ARMED_FOR_SHORT: ['等待做空', '当前价格在 ATR 线上方，向下穿越后建立空头'],
  WAITING_FOR_RESET: ['等待重新武装', '当前价格虽高于 ATR，但没有新的下方到上方穿越'],
  WAITING_FOR_DAILY_CLOSE: ['等待首个日线收盘', '预热已完成；首个完整前向 UTC 日尚未结束，当前没有组合持仓'],
  PORTFOLIO_ACTIVE: ['组合日线运行中', '按冻结月历映射监控状态袖套与三个趋势袖套'],
} as const

const triggerText: Record<string, string> = {
  RESUME_TRADING: '恢复策略执行',
  WAIT_FOR_ATR: '等待 ATR 计算完成',
  NEXT_TICK_FILL: '等待模拟撮合完成',
  PRICE_CROSS_BELOW: '价格实时向下穿越 ATR 线',
  NEXT_BAR_AND_FRESH_UP_CROSS: '进入下一根 K 线，并出现新的向上穿越',
  NEXT_BAR: '等待下一根 15 分钟 K 线',
  NEXT_BAR_REVERSAL_CONFIRMATION: '下一根 K 线继续突破 0.25 ATR',
  TREND_FILTER_RECOVERY: '趋势效率恢复至开仓阈值',
  PRICE_CROSS_ABOVE: '价格实时向上穿越 ATR 线',
  PRICE_BELOW_THEN_CROSS_ABOVE: '价格先回到 ATR 线下方，再重新上穿',
  NEXT_UTC_DAILY_CLOSE: '等待下一根完整 UTC 日线收盘',
  NEXT_UTC_MONTH_OPEN: '下一 UTC 自然月开盘自动恢复外层敞口',
}

const crossReasonText: Record<string, string> = {
  TRADING_PAUSED: '策略暂停',
  ALREADY_LONG: '已有多头持仓',
  ALREADY_SHORT: '已有空头持仓',
  ORDER_PENDING: '已有待成交订单',
  ACTION_LOCKED_THIS_BAR: '本 K 线已经执行一次交易',
  LOW_TREND_EFFICIENCY: '趋势效率不足',
  TREND_FILTER_WARMING: '趋势过滤预热中',
  REVERSAL_CANCELED_OPPOSITE_CROSS: '反向确认因价格反向穿越而取消',
  NO_POSITION: '没有可卖持仓',
  EXIT_LOCKED_THIS_BAR: '本 K 线卖出锁',
}

function DecisionStatus({ runtime }: { runtime: Account['runtime'] }) {
  const decision = runtime.decision
  const strategy = runtime.strategy
  const isPortfolio = runtime.paper_model === 'portfolio'
  const portfolioLockReason = runtime.market_state.month_lock_reason
  const copy = isPortfolio && decision.state === 'PAUSED' && runtime.market_state.month_locked
    ? [
        portfolioLockReason === 'UTC_MONTHLY_LOSS_LOCK' ? '月度亏损锁定' : '月度利润锁定',
        '组合有效外层敞口已归零；内部袖套仅作为影子账本继续更新，不承担组合收益风险',
      ]
    : decisionText[decision.state] ?? [
        '状态同步中',
        '前后端策略状态版本暂时不一致，等待服务刷新',
      ]
  const tone = decision.state === 'ARMED_FOR_BUY'
    || decision.state === 'ARMED_FOR_LONG'
    || decision.state === 'ARMED_FOR_SHORT'
    || decision.state === 'HOLDING_LONG'
    || decision.state === 'HOLDING_SHORT'
    || decision.state === 'PORTFOLIO_ACTIVE'
    ? 'ready'
    : decision.state === 'PAUSED'
      ? 'blocked'
      : 'waiting'
  const lastCross = isPortfolio
    ? decision.last_signal
      ? `最近组合信号 · ${time(decision.last_signal.timestamp_ms)}`
      : '尚无完整前向日信号'
    : strategy.last_cross
    ? `${strategy.last_cross === 'UP' ? '向上穿越' : '向下穿越'} · ${
        strategy.last_cross_result === 'BUY_SIGNAL'
          ? '已发出买入信号'
          : strategy.last_cross_result === 'SELL_SIGNAL'
            ? '已发出卖出信号'
            : `已拦截：${crossReasonText[strategy.last_cross_reason ?? ''] ?? strategy.last_cross_reason ?? '--'}`
      } · ${time(strategy.last_cross_at_ms)}`
    : decision.last_signal
      ? `${decision.last_signal.side === 'BUY' ? '买入' : '卖出'}信号 · ${decision.last_signal.status} · ${time(decision.last_signal.timestamp_ms)}`
      : '暂无穿越或交易信号'

  return (
    <section className={`decision-panel ${tone}`}>
      <div className="decision-state">
        <Crosshair size={19} />
        <div><span>交易决策状态</span><strong>{copy[0]}</strong></div>
      </div>
      <p>{copy[1]}</p>
      <dl className="decision-facts">
        <div><dt>仓位</dt><dd>{decision.position_side === 'LONG' ? '多头持仓' : decision.position_side === 'SHORT' ? '空头持仓' : '空仓'}</dd></div>
        <div><dt>订单</dt><dd>{decision.has_pending_order ? '待成交' : '无待成交'}</dd></div>
        <div><dt>{isPortfolio ? '最近日线信号' : '最近 Tick 穿越'}</dt><dd>{lastCross}</dd></div>
        <div><dt>下一触发条件</dt><dd>{triggerText[decision.next_trigger] ?? decision.next_trigger}</dd></div>
      </dl>
      <div className="decision-gates" aria-label="买入门控">
        <Gate open={decision.trading_enabled} label="策略运行" />
        <Gate open={decision.strategy_ready} label={isPortfolio ? '组合预热就绪' : 'ATR 就绪'} />
        <Gate open={decision.trend_filter_passed} label={isPortfolio ? '月历映射就绪' : '趋势过滤通过'} />
        <Gate open={!decision.has_pending_order} label="无待成交单" />
        <Gate open={decision.action_lock_open} label={isPortfolio ? '月度锁未触发' : '本 K 线动作锁开放'} />
        <Gate open={isPortfolio ? decision.signal_confirmation === 'DAILY_CLOSE' : decision.signal_confirmation === 'TICK'} label={isPortfolio ? 'UTC 日线检测' : 'Tick 实时检测'} />
      </div>
    </section>
  )
}

function Gate({ open, label }: { open: boolean; label: string }) {
  return <span className={open ? 'open' : 'closed'}>{open ? <CircleCheck size={13} /> : <CircleX size={13} />}{label}</span>
}

function CompletedTradeTable({ rows, currency }: { rows: CompletedTrade[]; currency: string }) {
  if (!rows.length) return <div className="empty-table">暂无完整交易</div>
  return (
    <div className="table-scroll">
      <table>
        <thead><tr><th>方向</th><th>动作顺序</th><th>开仓时间</th><th>平仓时间</th><th>开仓价</th><th>平仓价</th><th>数量</th><th>总手续费</th><th>资金费</th><th>净盈亏（含费）</th><th>收益率</th></tr></thead>
        <tbody>{rows.map((row) => {
          const profitable = row.netPnl >= 0
          const direction = row.direction
          const execution = `${row.direction} -> CLOSE`
          return (
            <tr key={`${row.entryTimestamp}-${row.exitTimestamp}-${row.direction}`} data-testid="completed-trade-row">
              <td><span className={`position-side ${row.direction.toLowerCase()}`}>{direction}</span></td>
              <td>{execution}</td>
              <td>{time(row.entryTimestamp)}</td>
              <td>{time(row.exitTimestamp)}</td>
              <td>{number(row.entryPrice, 4)}</td>
              <td>{number(row.exitPrice, 4)}</td>
              <td>{number(row.quantity, 3)}</td>
              <td>{money(row.fees, currency)}</td>
              <td className={row.fundingPnl >= 0 ? 'good-text' : 'bad-text'}>{money(row.fundingPnl, currency)}</td>
              <td className={profitable ? 'good-text' : 'bad-text'}><strong>{money(row.netPnl, currency)}</strong></td>
              <td className={profitable ? 'good-text' : 'bad-text'}><strong>{percent(row.returnPercent)}</strong></td>
            </tr>
          )
        })}</tbody>
      </table>
    </div>
  )
}

function Warehouse({
  summary,
  bars,
  trades,
  instrumentId,
}: {
  summary?: WarehouseSummary
  bars: OhlcvBar[]
  trades: AggTrade[]
  instrumentId?: string
}) {
  if (!summary) return <div className="loading"><Database size={20} />正在读取仓库</div>
  const instrument = summary.instruments.find((item) => item.instrument_id === instrumentId)
  const tableRows = [...summary.tables].sort((left, right) => right.size_bytes - left.size_bytes)
  return (
    <>
      <section className="warehouse-metrics">
        <WarehouseStat label="SQLite 总占用" value={bytes(summary.database.total_bytes)} sub={`主库 ${bytes(summary.database.main_bytes)} · WAL ${bytes(summary.database.wal_bytes)}`} />
        <WarehouseStat label="aggTrade" value={number(instrument?.agg_trades.row_count ?? 0, 0)} sub={`${number(instrument?.agg_trades.raw_trade_count ?? 0, 0)} 笔底层成交`} />
        <WarehouseStat label="15m OHLCV" value={number(instrument?.ohlcv.row_count ?? 0, 0)} sub={`${number(instrument?.ohlcv.closed_count ?? 0, 0)} 已收盘 · ${number(instrument?.ohlcv.open_count ?? 0, 0)} 形成中`} />
        <WarehouseStat label="最近写入" value={time(instrument?.agg_trades.last_timestamp_ms ?? null)} sub={`更新于 ${time(summary.generated_at_ms)}`} />
      </section>

      <section className="warehouse-data-grid">
        <div className="panel warehouse-table-panel">
          <div className="panel-head"><div><span>OHLCV ARCHIVE</span><h3>最近 15 分钟 K 线</h3></div><strong>{bars.length} rows</strong></div>
          <div className="table-scroll"><table><thead><tr><th>开始</th><th>状态</th><th>开</th><th>高</th><th>低</th><th>收</th><th>成交量</th><th>成交数</th></tr></thead><tbody>{bars.map((bar) => <tr key={bar.start_ms}><td>{time(bar.start_ms)}</td><td><span className={`bar-state ${bar.is_closed ? 'closed' : 'live'}`}>{bar.is_closed ? 'CLOSED' : 'LIVE'}</span></td><td>{number(bar.open, 4)}</td><td>{number(bar.high, 4)}</td><td>{number(bar.low, 4)}</td><td>{number(bar.close, 4)}</td><td>{number(bar.volume, 3)}</td><td>{number(bar.trade_count, 0)}</td></tr>)}</tbody></table></div>
        </div>

        <div className="panel warehouse-table-panel">
          <div className="panel-head"><div><span>BINANCE AGGTRADE</span><h3>最近聚合成交</h3></div><strong>{trades.length} rows</strong></div>
          <div className="table-scroll"><table><thead><tr><th>成交时间</th><th>方向</th><th>聚合 ID</th><th>底层 ID</th><th>价格</th><th>数量</th><th>名义金额</th><th>入库延迟</th></tr></thead><tbody>{trades.map((trade) => {
            const side = trade.buyer_is_maker === null ? null : trade.buyer_is_maker ? 'SELL' : 'BUY'
            const eventTime = trade.event_time_ms ?? trade.timestamp_ms
            return <tr key={trade.event_id}><td>{time(trade.timestamp_ms)}</td><td>{side ? <span className={`side ${side.toLowerCase()}`}>{side}</span> : '--'}</td><td>{trade.aggregate_trade_id ?? '--'}</td><td>{trade.first_trade_id ?? '--'}–{trade.last_trade_id ?? '--'}</td><td>{number(trade.price, 4)}</td><td>{number(trade.quantity, 3)}</td><td>{money(trade.notional, 'USDT')}</td><td>{number(Math.max(0, trade.received_at_ms - eventTime), 0)} ms</td></tr>
          })}</tbody></table></div>
        </div>
      </section>

      <section className="panel warehouse-storage">
        <div className="panel-head"><div><span>STORAGE FOOTPRINT</span><h3>SQLite 分表占用</h3></div><span className="warehouse-path">{summary.database.path}</span></div>
        <div className="table-scroll"><table><thead><tr><th>数据表</th><th>记录数</th><th>数据与索引</th><th>平均每行</th></tr></thead><tbody>{tableRows.map((table) => <tr key={table.name}><td>{table.name}</td><td>{number(table.row_count, 0)}</td><td>{bytes(table.size_bytes)}</td><td>{bytes(table.average_row_bytes)}</td></tr>)}</tbody></table></div>
      </section>
    </>
  )
}

function WarehouseStat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return <div className="warehouse-stat"><span>{label}</span><strong>{value}</strong><small>{sub}</small></div>
}

const portfolioEventLabels: Record<string, string> = {
  FUNDING: '资金费结算',
  OUTER_EXPOSURE_REBALANCE: '外层敞口调整',
  STATE_EXPOSURE_REBALANCE: '状态敞口调整',
  CALENDAR_ROUTE_REBALANCE: '月历路由换仓',
}

function portfolioEventLabel(event: PortfolioSleeveEvent) {
  if (event.event_type === 'OUTER_EXPOSURE_REBALANCE') {
    if (Number(event.payload.target_after) === 0) return '外层组合平仓'
    if (Number(event.payload.target_before) === 0) return '外层组合恢复'
  }
  return portfolioEventLabels[event.event_type] ?? event.event_type
}

function portfolioEventDetail(event: PortfolioSleeveEvent) {
  const payload = event.payload
  if (event.event_type === 'FUNDING') {
    return `数量 ${number(payload.quantity ?? null, 4)} · 费率 ${precisePercent(Number(payload.rate ?? 0), 4)} · 金额 ${money(payload.amount ?? null, 'USDT')}`
  }
  if (event.event_type.includes('REBALANCE')) {
    const targets = payload.target_before !== undefined || payload.target_after !== undefined
      ? `敞口 ${number(payload.target_before ?? null, 2)}x → ${number(payload.target_after ?? null, 2)}x · `
      : ''
    return `${targets}换手 ${number(payload.turnover ?? null, 4)} · 路由成本 ${precisePercent(-Number(payload.route_cost ?? 0), 4)}`
  }
  return `目标 ${number(payload.target_before ?? null, 2)} → ${number(payload.target_after ?? null, 2)}`
}

function PortfolioOrders({ events, loading }: { events: PortfolioSleeveEvent[]; loading: boolean }) {
  const fills = [...events]
    .filter((event) => event.event_type === 'COMPONENT_FILL')
    .reverse()
  const accountingEvents = [...events]
    .filter((event) => event.event_type !== 'COMPONENT_FILL')
    .reverse()

  if (loading && !events.length) {
    return <div className="loading"><ListOrdered size={20} />正在读取基础袖套账本</div>
  }

  return (
    <>
      <section className="panel full-table portfolio-order-ledger" aria-label="袖套成交订单">
        <div className="panel-head">
          <div><span>BASE LEDGER / MODEL FILLS</span><h3>袖套成交订单</h3></div>
          <span>{fills.length} fills · 不含压力账本</span>
        </div>
        {!fills.length ? <div className="empty-table">暂无袖套成交</div> : (
          <div className="table-scroll"><table><thead><tr><th>成交时间（UTC）</th><th>袖套</th><th>组合基准权重</th><th>品种</th><th>方向</th><th>仓位动作</th><th>数量</th><th>市场价</th><th>成交价</th><th>手续费</th><th>滑点</th></tr></thead><tbody>
            {fills.map((event) => {
              const weight = portfolioSleeveWeight(event)
              const payload = event.payload
              return <tr key={`${event.day}-${event.event_index}`}>
                <td>{utcTimestamp(event.timestamp_ms)}</td>
                <td className="portfolio-order-sleeve"><span>{event.day}</span><strong>{portfolioSleeveLabel(event.sleeve_id)}</strong></td>
                <td><strong className="portfolio-weight">{weight === null ? '--' : `${(weight * 100).toFixed(2)}%`}</strong></td>
                <td>{event.instrument_id === 'btc_perp' ? 'BTCUSDT' : event.instrument_id === 'eth_perp' ? 'ETHUSDT' : '--'}</td>
                <td><span className={`side ${(payload.side ?? '').toLowerCase()}`}>{payload.side ?? '--'}</span></td>
                <td>{payload.position_effect === 'OPEN' ? '开仓' : payload.position_effect === 'CLOSE' ? '平仓' : '--'}</td>
                <td>{number(payload.quantity ?? null, 4)}</td>
                <td>{money(payload.market_price ?? null, 'USDT')}</td>
                <td>{money(payload.fill_price ?? null, 'USDT')}</td>
                <td>{money(payload.fee ?? null, 'USDT')}</td>
                <td>{money(payload.slippage ?? null, 'USDT')}</td>
              </tr>
            })}
          </tbody></table></div>
        )}
        <p className="portfolio-ledger-note">组合模拟器在下一 UTC 日开盘直接生成确定性模型成交，因此没有传统的 PENDING 订单阶段；这里展示基础成本账本的 COMPONENT_FILL。</p>
      </section>

      <section className="panel full-table portfolio-event-ledger" aria-label="组合账本事件">
        <div className="panel-head">
          <div><span>BASE LEDGER / AUDIT EVENTS</span><h3>资金费与敞口事件</h3></div>
          <span>{accountingEvents.length} events</span>
        </div>
        {!accountingEvents.length ? <div className="empty-table">暂无其他组合事件</div> : (
          <div className="table-scroll"><table><thead><tr><th>时间（UTC）</th><th>事件</th><th>袖套</th><th>品种</th><th>明细</th></tr></thead><tbody>
            {accountingEvents.map((event) => <tr key={`${event.day}-${event.event_index}`}>
              <td>{utcTimestamp(event.timestamp_ms)}</td>
              <td>{portfolioEventLabel(event)}</td>
              <td>{portfolioSleeveLabel(event.sleeve_id)}</td>
              <td>{event.instrument_id === 'btc_perp' ? 'BTCUSDT' : event.instrument_id === 'eth_perp' ? 'ETHUSDT' : 'PORTFOLIO'}</td>
              <td>{portfolioEventDetail(event)}</td>
            </tr>)}
          </tbody></table></div>
        )}
      </section>
    </>
  )
}

function Orders({ rows }: { rows: Order[] }) {
  return <section className="panel full-table"><div className="panel-head"><div><span>ORDER LEDGER</span><h3>全部订单</h3></div><span>{rows.length} records</span></div>{!rows.length ? <div className="empty-table">暂无订单</div> : <div className="table-scroll"><table><thead><tr><th>提交时间</th><th>账户</th><th>方向</th><th>状态</th><th>信号价</th><th>成交价</th><th>ATR</th><th>原因</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{time(row.submitted_at_ms)}</td><td>{row.account_id.toUpperCase()}</td><td><span className={`side ${row.side.toLowerCase()}`}>{row.side}</span></td><td>{row.status}</td><td>{number(row.signal_price, 4)}</td><td>{number(row.fill_price, 4)}</td><td>{number(row.atr, 4)}</td><td>{row.reason}</td></tr>)}</tbody></table></div>}</section>
}

function Returns({
  account,
  summary,
  loading,
}: {
  account?: Account
  summary?: ReturnSummary
  loading: boolean
}) {
  if (loading || !account || !summary) {
    return <div className="loading"><CalendarDays size={20} />正在计算收益明细</div>
  }
  const leadingDays = summary.daily.length
    ? (new Date(summary.daily[0].start_ms).getDay() + 6) % 7
    : 0
  const weekly = [...summary.weekly].reverse()
  const monthly = [...summary.monthly].reverse()

  return (
    <>
      <section className="return-metrics">
        <ReturnStat label="近 30 日收益" value={summary.return_30d} sub={`截至 ${returnDate(summary.as_of_ms)}`} />
        <ReturnStat label="本周收益" value={summary.current_week_return} sub="周一至当前" />
        <ReturnStat label="本月收益" value={summary.current_month_return} sub={returnMonth(summary.as_of_ms)} />
        <ReturnStat label="年化收益" value={summary.annualized_return} sub={`成立以来 CAGR · ${summary.elapsed_days.toFixed(1)} 天`} />
      </section>

      <section className="panel return-calendar-panel">
        <div className="panel-head">
          <div><span>DAILY PERFORMANCE</span><h3>最近 30 天收益日历</h3></div>
          <strong className={returnTone(summary.total_return)}>累计 {percent(summary.total_return)}</strong>
        </div>
        <div className="return-calendar">
          <div className="calendar-weekdays">
            {['一', '二', '三', '四', '五', '六', '日'].map((day) => <span key={day}>周{day}</span>)}
          </div>
          <div className="calendar-grid">
            {Array.from({ length: leadingDays }, (_, index) => <i key={`blank-${index}`} />)}
            {summary.daily.map((period, index) => (
              <div
                className={`calendar-day ${returnTone(period.return)} ${index === summary.daily.length - 1 ? 'current' : ''}`}
                key={period.key}
              >
                <time>{calendarDate(period.start_ms)}</time>
                <strong>{returnValue(period.return)}</strong>
                <small>{period.equity === null ? '无数据' : money(period.equity, account.currency)}</small>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="return-period-grid">
        <ReturnPeriodTable title="每周收益" kicker="WEEKLY RETURNS" rows={weekly} currency={account.currency} />
        <ReturnPeriodTable title="每月收益" kicker="MONTHLY RETURNS" rows={monthly} currency={account.currency} />
      </section>
    </>
  )
}

function ReturnStat({ label, value, sub }: { label: string; value: number | null; sub: string }) {
  return <div className={`return-stat ${returnTone(value)}`}><span>{label}</span><strong>{returnValue(value)}</strong><small>{sub}</small></div>
}

function ReturnPeriodTable({
  title,
  kicker,
  rows,
  currency,
}: {
  title: string
  kicker: string
  rows: ReturnPeriod[]
  currency: string
}) {
  return (
    <div className="panel return-period-panel">
      <div className="panel-head"><div><span>{kicker}</span><h3>{title}</h3></div><span>{rows.length} periods</span></div>
      {!rows.length ? <div className="empty-table">暂无收益数据</div> : (
        <div className="table-scroll"><table><thead><tr><th>周期</th><th>区间</th><th>期末净值</th><th>收益率</th></tr></thead><tbody>{rows.map((row) => (
          <tr key={row.key}>
            <td>{row.label}</td>
            <td>{returnDate(row.start_ms)} - {returnDate(Math.min(row.end_ms - 1, Date.now()))}</td>
            <td>{money(row.equity, currency)}</td>
            <td><strong className={returnTone(row.return)}>{returnValue(row.return)}</strong></td>
          </tr>
        ))}</tbody></table></div>
      )}
    </div>
  )
}

function returnTone(value: number | null) {
  if (value === null || value === 0) return 'neutral-return'
  return value > 0 ? 'positive-return' : 'negative-return'
}

function returnDate(value: number) {
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(value)
}

function returnMonth(value: number) {
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long' }).format(value)
}

function calendarDate(value: number) {
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit' }).format(value)
}

export default App
