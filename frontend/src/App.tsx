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
  Gauge,
  ListOrdered,
  Maximize2,
  Radio,
  RefreshCw,
  Target,
  TrendingDown,
  TrendingUp,
  WalletCards,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Brush,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from './api'
import type {
  Account,
  AggTrade,
  EquityPoint,
  Fill,
  FundingPayment,
  OhlcvBar,
  Order,
  ReturnPeriod,
  ReturnSummary,
  WarehouseSummary,
} from './types'

type View = 'monitor' | 'orders' | 'returns' | 'warehouse'

type PricePoint = {
  timestamp: number
  price: number
  trailingStop: number | null
}

type TradePoint = {
  timestamp: number
  price: number
  side: 'BUY' | 'SELL'
}

type CompletedTrade = {
  entryTimestamp: number
  exitTimestamp: number
  entryPrice: number
  exitPrice: number
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

function App() {
  const client = useQueryClient()
  const [view, setView] = useState<View>('monitor')
  const [accountId, setAccountId] = useState('soxl_perp')
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview, refetchInterval: 1000 })
  const equity = useQuery({ queryKey: ['equity', accountId], queryFn: () => api.equity(accountId), refetchInterval: 5000 })
  const fills = useQuery({ queryKey: ['fills', accountId], queryFn: () => api.fills(accountId), refetchInterval: 5000 })
  const funding = useQuery({ queryKey: ['funding', accountId], queryFn: () => api.funding(accountId), refetchInterval: 5000 })
  const orders = useQuery({ queryKey: ['orders', accountId], queryFn: () => api.orders(accountId), refetchInterval: 5000 })
  const returns = useQuery({
    queryKey: ['returns', accountId],
    queryFn: () => api.returns(accountId),
    enabled: view === 'returns',
    refetchInterval: 60_000,
  })
  const warehouse = useQuery({
    queryKey: ['warehouse'],
    queryFn: api.warehouse,
    enabled: view === 'warehouse',
    refetchInterval: 5000,
  })
  const aggTrades = useQuery({
    queryKey: ['agg-trades', accountId],
    queryFn: () => api.aggTrades(accountId),
    enabled: view === 'warehouse',
  })
  const ohlcv = useQuery({
    queryKey: ['ohlcv', accountId],
    queryFn: () => api.ohlcv(accountId),
    refetchInterval: 5000,
  })
  const control = useMutation({
    mutationFn: api.control,
    onSuccess: () => client.invalidateQueries({ queryKey: ['overview'] }),
  })

  const accounts = overview.data?.accounts ?? []
  const account = accounts.find((item) => item.id === accountId) ?? accounts[0]
  const liveCount = overview.data?.instruments.filter((item) => item.status === 'LIVE').length ?? 0
  const instrumentCount = overview.data?.instruments.length ?? 1
  const pageTitle = view === 'warehouse' ? '数据仓库' : view === 'returns' ? '收益明细' : '实时模拟盘'
  const pageKicker = view === 'warehouse'
    ? 'MARKET DATA / SQLITE WAL'
    : view === 'returns'
      ? 'PERFORMANCE / CALENDAR RETURNS'
      : 'SHORT-HORIZON EXECUTION / ATR TICK V1'

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
          <span className="paper-badge">PAPER</span>
          <span className={`feed-state ${liveCount ? 'live' : ''}`}>
            <i />{liveCount}/{instrumentCount} LIVE
          </span>
          <button
            className={overview.data?.trading_enabled ? 'control danger' : 'control resume'}
            disabled={control.isPending}
            onClick={() => control.mutate(overview.data?.trading_enabled ? 'pause' : 'resume')}
            title={overview.data?.trading_enabled ? '暂停策略' : '恢复策略'}
          >
            {overview.data?.trading_enabled ? <CirclePause size={17} /> : <CirclePlay size={17} />}
            {overview.data?.trading_enabled ? '暂停' : '恢复'}
          </button>
        </div>
      </header>

      <main>
        <section className="page-head">
          <div>
            <div className="kicker">{pageKicker}</div>
            <h1>{pageTitle}</h1>
          </div>
          <div className="page-actions">
            <div className="account-switch" aria-label="模拟账户">
              {accounts.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={item.id === account?.id ? 'active' : ''}
                  onClick={() => setAccountId(item.id)}
                >
                  {item.display_symbol}
                </button>
              ))}
            </div>
            <div className="scope-chips">
              <span>{account?.symbol ?? 'INITIALIZING'}</span><span>15m</span><span>{view === 'warehouse' ? 'TICK ARCHIVE' : view === 'returns' ? 'PERFORMANCE' : account?.runtime.paper_model === 'futures' ? 'LONG / SHORT' : 'LONG ONLY'}</span>
            </div>
          </div>
        </section>

        {overview.isError ? (
          <div className="error-state">
            API 无法连接
            <button onClick={() => overview.refetch()}><RefreshCw size={15} />重试</button>
          </div>
        ) : view === 'monitor' ? (
          <Monitor account={account} equity={equity.data ?? []} fills={fills.data ?? []} funding={funding.data ?? []} bars={ohlcv.data ?? []} />
        ) : view === 'orders' ? (
          <Orders rows={orders.data ?? []} />
        ) : view === 'returns' ? (
          <Returns account={account} summary={returns.data} loading={returns.isLoading} />
        ) : (
          <Warehouse
            summary={warehouse.data}
            bars={ohlcv.data ?? []}
            trades={aggTrades.data ?? []}
            instrumentId={account?.id}
          />
        )}
      </main>
      <footer>
        <span>mastermind:tick v0.1</span>
        <span>本地模拟撮合 · 非真实账户</span>
      </footer>
    </div>
  )
}

function Monitor({
  account,
  equity,
  fills,
  funding,
  bars,
}: {
  account?: Account
  equity: EquityPoint[]
  fills: Fill[]
  funding: FundingPayment[]
  bars: OhlcvBar[]
}) {
  const accountFills = useMemo(
    () => fills.filter((fill) => fill.account_id === account?.id),
    [fills, account?.id],
  )
  const priceChart = useMemo(() => buildPriceChart(equity), [equity])
  const klineChart = useMemo(() => buildKlineChart(bars), [bars])
  const tradeMarkers = useMemo(() => buildTradeMarkers(accountFills), [accountFills])
  const completedTrades = useMemo(
    () => buildCompletedTrades(accountFills, funding),
    [accountFills, funding],
  )
  const [priceRange, setPriceRange] = useChartRange(priceChart.length)
  const [klineRange, setKlineRange] = useChartRange(
    klineChart.length,
    DEFAULT_VISIBLE_KLINES,
  )
  const visibleStart = priceChart[priceRange.startIndex]
  const visibleEnd = priceChart[priceRange.endIndex]
  const visibleTradeMarkers = useMemo(
    () => tradeMarkers.filter((point) => (
      visibleStart && visibleEnd
        ? point.timestamp >= visibleStart.timestamp && point.timestamp <= visibleEnd.timestamp
        : true
    )),
    [tradeMarkers, visibleStart, visibleEnd],
  )
  const visibleCompletedTrades = useMemo(
    () => completedTrades.filter((trade) => (
      visibleStart && visibleEnd
        ? trade.exitTimestamp >= visibleStart.timestamp && trade.entryTimestamp <= visibleEnd.timestamp
        : true
    )),
    [completedTrades, visibleStart, visibleEnd],
  )
  const visibleKlines = klineChart.slice(klineRange.startIndex, klineRange.endIndex + 1)
  const visibleKlineStart = visibleKlines[0]
  const visibleKlineEnd = visibleKlines[visibleKlines.length - 1]
  const visibleKlineTrades = useMemo(
    () => accountFills.filter((fill) => (
      visibleKlineStart && visibleKlineEnd
        ? fill.timestamp_ms >= visibleKlineStart.timestamp && fill.timestamp_ms <= visibleKlineEnd.endTimestamp
        : true
    )),
    [accountFills, visibleKlineStart, visibleKlineEnd],
  )
  const equityChart = useMemo(
    () => equity.map((point) => ({ timestamp: point.timestamp_ms, equity: Number(point.equity) })),
    [equity],
  )
  if (!account) return <div className="loading"><Activity size={20} />正在初始化账户</div>
  const runtime = account.runtime
  const strategy = runtime.strategy
  const positionQuantity = Number(account.quantity)
  const positionSide = positionQuantity > 0 ? '多头' : positionQuantity < 0 ? '空头' : '空仓'
  const positive = Number(account.total_pnl) >= 0
  const periodReturn = visibleStart && visibleEnd && visibleStart.price
    ? visibleEnd.price / visibleStart.price - 1
    : 0

  const panPriceChart = (direction: -1 | 1) => {
    setPriceRange((current) => panRange(current, priceChart.length, direction))
  }
  const zoomPriceChart = (factor: number) => {
    setPriceRange((current) => zoomRange(current, priceChart.length, factor))
  }

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
          <strong>{money(account.last_price, account.currency)}</strong>
          <span><Clock3 size={13} />{time(account.last_snapshot_ms)}</span>
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
        <Metric label="账户净值" value={money(account.equity, account.currency)} sub={`初始 ${money(account.initial_cash, account.currency)}`} icon={<WalletCards />} />
        <Metric label="累计收益" value={percent(account.total_return)} sub={money(account.total_pnl, account.currency)} tone={positive ? 'good' : 'bad'} icon={positive ? <TrendingUp /> : <TrendingDown />} />
        <Metric label="当前持仓" value={number(Math.abs(positionQuantity))} sub={`${positionSide} · 均价 ${money(account.average_price, account.currency)}`} icon={<Activity />} />
        <Metric label="最大回撤" value={percent(account.max_drawdown)} sub={`${account.round_trips} 次完整交易`} tone="bad" icon={<TrendingDown />} />
        <Metric label="夏普率" value={number(account.sharpe_ratio, 2)} sub="15m 年化 · rf 0%" tone={account.sharpe_ratio === null ? '' : account.sharpe_ratio >= 0 ? 'good' : 'bad'} icon={<Gauge />} />
        <Metric label="交易胜率" value={account.win_rate === null ? '--' : rate(account.win_rate)} sub={`${account.winning_trades} 赢 / ${account.round_trips} 笔`} tone={account.win_rate === null ? '' : account.win_rate >= 0.5 ? 'good' : 'bad'} icon={<Target />} />
      </section>

      <DecisionStatus runtime={runtime} />

      <section className="workspace-grid">
        <div className="panel price-panel">
          <div className="panel-head price-head">
            <div><span>PRICE / ATR TRAILING STOP</span><h3>价格与交易信号</h3></div>
            <div className="chart-summary">
              <div className="chart-controls" aria-label="价格图时间窗口">
                <button type="button" onClick={() => panPriceChart(-1)} title="向左滚动" aria-label="向左滚动价格图"><ChevronLeft size={15} /></button>
                <button type="button" onClick={() => panPriceChart(1)} title="向右滚动" aria-label="向右滚动价格图"><ChevronRight size={15} /></button>
                <button type="button" onClick={() => zoomPriceChart(0.65)} title="放大" aria-label="放大价格图"><ZoomIn size={15} /></button>
                <button type="button" onClick={() => zoomPriceChart(1.55)} title="缩小" aria-label="缩小价格图"><ZoomOut size={15} /></button>
                <button type="button" onClick={() => setPriceRange(fullRange(priceChart.length))} title="显示全部" aria-label="显示全部价格数据"><Maximize2 size={14} /></button>
              </div>
              <div className="chart-legend">
                <span><i className="price-dot" />价格</span>
                <span><i className="atr-dot" />ATR 止损线</span>
              </div>
              <strong className={periodReturn >= 0 ? 'good-text' : 'bad-text'}>{percent(periodReturn)}</strong>
            </div>
          </div>
          <div className="price-chart-wrap">
            {priceChart.length > 1 ? (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={priceChart} margin={{ top: 24, right: 24, bottom: 2, left: 2 }}>
                  <CartesianGrid stroke="#20272d" vertical={false} />
                  <XAxis
                    dataKey="timestamp"
                    type="number"
                    domain={['dataMin', 'dataMax']}
                    stroke="#66727d"
                    tickLine={false}
                    axisLine={false}
                    minTickGap={70}
                    fontSize={11}
                    tickFormatter={(value) => time(Number(value))}
                  />
                  <YAxis
                    domain={['auto', 'auto']}
                    stroke="#66727d"
                    tickLine={false}
                    axisLine={false}
                    width={58}
                    fontSize={11}
                    tickFormatter={(value) => Number(value).toFixed(2)}
                  />
                  <Tooltip
                    content={(
                      <PriceTooltip
                        currency={account.currency}
                        pricePoints={priceChart}
                        tradePoints={tradeMarkers}
                        referencePrice={visibleStart?.price ?? null}
                      />
                    )}
                  />
                  {visibleCompletedTrades.map((trade) => {
                    const profitable = trade.netPnl >= 0
                    const color = profitable ? '#3fd6a1' : '#ff6f78'
                    return (
                      <ReferenceArea
                        key={`${trade.entryTimestamp}-${trade.exitTimestamp}`}
                        className={`trade-range ${profitable ? 'profit' : 'loss'}`}
                        x1={trade.entryTimestamp}
                        x2={trade.exitTimestamp}
                        y1={trade.entryPrice}
                        y2={trade.exitPrice}
                        fill={color}
                        fillOpacity={0.2}
                        stroke={color}
                        strokeOpacity={0.95}
                        strokeWidth={2}
                        strokeDasharray="4 3"
                        ifOverflow="extendDomain"
                        label={{
                          value: `${money(trade.netPnl, account.currency)} · ${percent(trade.returnPercent)}`,
                          position: 'center',
                          fill: color,
                          fontSize: 13,
                          fontWeight: 800,
                        }}
                      />
                    )
                  })}
                  <Line dataKey="price" name="价格" type="monotone" stroke="#e7ecef" strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Line dataKey="trailingStop" name="ATR 止损线" type="stepAfter" stroke="#e8bd58" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                  <Scatter
                    data={visibleTradeMarkers}
                    dataKey="price"
                    name="成交"
                    shape={(props: unknown) => <TradeMarker {...(props as TradeMarkerProps)} />}
                    isAnimationActive={false}
                  />
                  <Brush
                    dataKey="timestamp"
                    startIndex={priceRange.startIndex}
                    endIndex={priceRange.endIndex}
                    height={25}
                    travellerWidth={9}
                    stroke="#53616b"
                    fill="#0d1114"
                    tickFormatter={(value) => time(Number(value))}
                    onChange={(next) => {
                      if (typeof next.startIndex === 'number' && typeof next.endIndex === 'number') {
                        setPriceRange(clampRange(next, priceChart.length))
                      }
                    }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            ) : <div className="empty-chart">正在积累价格与 ATR 快照</div>}
          </div>
        </div>

        <div className="panel strategy-panel">
          <div className="panel-head">
            <div><span>STRATEGY STATE</span><h3>ATR 实时状态</h3></div>
            <span className={`relation ${strategy.relation}`}>{strategy.relation.toUpperCase()}</span>
          </div>
          <dl className="strategy-values">
            <div><dt>ATR(7)</dt><dd>{number(strategy.atr, 4)}</dd></div>
            <div><dt>ATR 止损线</dt><dd>{money(strategy.trailing_stop, account.currency)}</dd></div>
            <div><dt>价格距离</dt><dd>{strategy.price && strategy.trailing_stop ? money(Number(strategy.price) - Number(strategy.trailing_stop), account.currency) : '--'}</dd></div>
            <div><dt>K 线开始</dt><dd>{time(strategy.bar_start_ms)}</dd></div>
            <div><dt>K 线状态</dt><dd>形成中，Tick 实时交易</dd></div>
            <div><dt>信号检测</dt><dd>每个成交 Tick</dd></div>
            <div><dt>成交时点</dt><dd>下一成交 Tick</dd></div>
            <div><dt>{runtime.paper_model === 'futures' ? 'BUY 锁' : '买入锁'}</dt><dd>{strategy.bought_this_bar ? 'LOCKED' : 'OPEN'}</dd></div>
            <div><dt>{runtime.paper_model === 'futures' ? 'SELL 锁' : '空仓锁'}</dt><dd>{strategy.flattened_this_bar ? 'LOCKED' : 'OPEN'}</dd></div>
          </dl>
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
          <div className="strategy-foot">
            <span>15m</span>
            <span>{(runtime.position_fraction * 100).toFixed(0)}% {runtime.paper_model === 'futures' ? 'margin' : 'exposure'}</span>
            <span>{runtime.paper_model === 'futures' ? `${runtime.leverage}x ${runtime.margin_mode}` : 'SPOT'}</span>
            <span>TICK signals</span>
            <span>NEXT-TICK fills</span>
          </div>
        </div>
      </section>

      <OfficialKlinePanel
        account={account}
        bars={visibleKlines}
        fills={visibleKlineTrades}
        pointCount={klineChart.length}
        onRangeChange={setKlineRange}
      />

      <section className="lower-grid">
        <div className="panel fills-panel">
          <div className="panel-head">
            <div><span>EXECUTIONS</span><h3>最近成交</h3></div>
            <a className="export" href={`/api/fills.csv?account_id=${account.id}`}><ArrowDownToLine size={15} />CSV</a>
          </div>
          <FillTable rows={accountFills.slice(0, 8)} />
        </div>
        <div className="panel compact-equity-panel">
          <div className="panel-head"><div><span>ACCOUNT EQUITY</span><h3>净值曲线</h3></div><strong>{money(account.equity, account.currency)}</strong></div>
          <div className="compact-chart-wrap">
            {equityChart.length > 1 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={equityChart} margin={{ top: 12, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="#20272d" vertical={false} />
                  <XAxis dataKey="timestamp" hide />
                  <YAxis domain={['auto', 'auto']} hide />
                  <Tooltip labelFormatter={(value) => time(Number(value))} formatter={(value) => money(Number(value), account.currency)} />
                  <Line dataKey="equity" type="monotone" stroke="#3fd6a1" strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : <div className="empty-chart">等待净值快照</div>}
          </div>
        </div>
      </section>
    </>
  )
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

function OfficialKlinePanel({
  account,
  bars,
  fills,
  pointCount,
  onRangeChange,
}: {
  account: Account
  bars: KlinePoint[]
  fills: Fill[]
  pointCount: number
  onRangeChange: React.Dispatch<React.SetStateAction<ChartRange>>
}) {
  const pan = (direction: -1 | 1) => {
    onRangeChange((current) => panRange(current, pointCount, direction))
  }
  const zoom = (factor: number) => {
    onRangeChange((current) => zoomRange(current, pointCount, factor))
  }
  const validation = account.runtime.kline_state.validation

  return (
    <section className="panel kline-panel" data-testid="official-kline-panel">
      <div className="panel-head price-head">
        <div><span>BINANCE OFFICIAL 15M OHLCV</span><h3>官方 15 分钟 K线</h3></div>
        <div className="chart-summary">
          <div className="chart-controls" aria-label="K线时间窗口">
            <button type="button" onClick={() => pan(-1)} title="向左滚动K线" aria-label="向左滚动K线"><ChevronLeft size={15} /></button>
            <button type="button" onClick={() => pan(1)} title="向右滚动K线" aria-label="向右滚动K线"><ChevronRight size={15} /></button>
            <button type="button" onClick={() => zoom(0.65)} title="放大K线" aria-label="放大K线"><ZoomIn size={15} /></button>
            <button type="button" onClick={() => zoom(1.55)} title="缩小K线" aria-label="缩小K线"><ZoomOut size={15} /></button>
            <button type="button" onClick={() => onRangeChange(fullRange(pointCount))} title="显示全部K线" aria-label="显示全部K线"><Maximize2 size={14} /></button>
          </div>
          <div className="chart-legend kline-legend">
            <span><i className="candle-up" />上涨</span>
            <span><i className="candle-down" />下跌</span>
            <span className={`kline-validation ${validation.toLowerCase()}`}>{validation}</span>
          </div>
        </div>
      </div>
      <div className="kline-chart-wrap">
        {bars.length > 0 ? (
          <KlineSvg bars={bars} fills={fills} />
        ) : <div className="empty-chart">等待官方 15m K线</div>}
      </div>
    </section>
  )
}

function KlineSvg({ bars, fills }: { bars: KlinePoint[]; fills: Fill[] }) {
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
  const fillMarkers = fills.flatMap((fill) => {
    const index = bars.findIndex((bar) => fill.timestamp_ms >= bar.timestamp && fill.timestamp_ms <= bar.endTimestamp)
    if (index < 0) return []
    return [{ fill, index }]
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
        const color = up ? '#3fd6a1' : '#ff6f78'
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
      {fillMarkers.map(({ fill, index }) => {
        const center = x(index)
        const markerY = y(Number(fill.price))
        const buy = fill.side === 'BUY'
        const color = buy ? '#3fd6a1' : '#ff6f78'
        return (
          <g key={fill.id} className="kline-trade-marker" data-testid={`kline-marker-${fill.side.toLowerCase()}`}>
            <circle cx={center} cy={markerY} r={7} fill="#10151a" stroke={color} strokeWidth={2} />
            <path d={buy ? `M ${center - 3} ${markerY + 2} L ${center} ${markerY - 3} L ${center + 3} ${markerY + 2}` : `M ${center - 3} ${markerY - 2} L ${center} ${markerY + 3} L ${center + 3} ${markerY - 2}`} fill="none" stroke={color} strokeWidth={1.5} />
            <text x={center} y={buy ? markerY + 21 : markerY - 12} textAnchor="middle" fill={color} className="kline-trade-label">{fill.side}</text>
          </g>
        )
      })}
      {bars.length > 0 && <text x={left} y={height - 8} className="kline-axis-label">{time(bars[0].timestamp)}</text>}
      {bars.length > 1 && <text x={width - right} y={height - 8} textAnchor="end" className="kline-axis-label">{time(bars[bars.length - 1].timestamp)}</text>}
    </svg>
  )
}

function buildTradeMarkers(fills: Fill[]): TradePoint[] {
  return fills.map((fill) => ({
    timestamp: fill.timestamp_ms,
    price: Number(fill.price),
    side: fill.side as 'BUY' | 'SELL',
  }))
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
          entryTimestamp: entry.timestamp_ms,
          exitTimestamp: fill.timestamp_ms,
          entryPrice: Number(entry.price),
          exitPrice: Number(fill.price),
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
    const fundingPnl = funding
      .filter((payment) => payment.timestamp_ms >= entry!.timestamp_ms && payment.timestamp_ms <= fill.timestamp_ms)
      .reduce((total, payment) => total + Number(payment.amount), 0)
    const netPnl = (exitUnitProceeds - entryUnitCost) * matchedQuantity + fundingPnl
    trades.push({
      entryTimestamp: entry.timestamp_ms,
      exitTimestamp: fill.timestamp_ms,
      entryPrice: Number(entry.price),
      exitPrice: Number(fill.price),
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
}

function TradeMarker({ cx = 0, cy = 0, payload }: TradeMarkerProps) {
  if (!payload || (payload.side !== 'BUY' && payload.side !== 'SELL')) return null
  const isBuy = payload.side === 'BUY'
  const Icon = isBuy ? CircleArrowUp : CircleArrowDown
  const color = isBuy ? '#3fd6a1' : '#ff6f78'
  const label = isBuy ? 'BUY' : 'SELL'
  const labelY = isBuy ? cy + 30 : cy - 21
  return (
    <g data-testid={`trade-marker-${payload.side.toLowerCase()}`}>
      <Icon x={cx - 10} y={cy - 10} width={20} height={20} color={color} fill="#10151a" strokeWidth={2.2} />
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
  referencePrice,
}: {
  active?: boolean
  payload?: Array<{ payload?: PricePoint | TradePoint }>
  label?: number | string
  currency: string
  pricePoints: PricePoint[]
  tradePoints: TradePoint[]
  referencePrice: number | null
}) {
  const values = (payload ?? []).flatMap((item) => item.payload ? [item.payload] : [])
  const payloadPrice = values.find(isPricePoint)
  const payloadTrade = values.find(isTradePoint)
  const timestamp = Number(label ?? payloadPrice?.timestamp ?? payloadTrade?.timestamp)
  if (!active || !Number.isFinite(timestamp)) return null

  const point = payloadPrice ?? pricePoints.find((item) => item.timestamp === timestamp)
  const trade = payloadTrade ?? tradePoints.find((item) => item.timestamp === timestamp)
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
          <span>成交信号</span>
          <strong className={trade.side === 'BUY' ? 'good-text' : 'bad-text'}>
            {trade.side} · {money(trade.price, currency)}
          </strong>
        </div>
      )}
    </div>
  )
}

function isPricePoint(value: PricePoint | TradePoint): value is PricePoint {
  return 'trailingStop' in value
}

function isTradePoint(value: PricePoint | TradePoint): value is TradePoint {
  return 'side' in value
}

function Metric({ label, value, sub, tone = '', icon }: { label: string; value: string; sub: string; tone?: string; icon: React.ReactNode }) {
  return <div className={`metric ${tone}`}><div className="metric-top"><span>{label}</span>{icon}</div><strong>{value}</strong><small>{sub}</small></div>
}

const decisionText = {
  PAUSED: ['策略已暂停', '行情和 ATR 继续更新，但不会发出交易信号'],
  WARMING_UP: ['ATR 预热中', '历史 K 线不足，暂时不能判断穿越'],
  ORDER_PENDING: ['订单待成交', '信号已提交，正在等待下一个 Tick 模拟撮合'],
  HOLDING_LONG: ['持仓监控中', '已持有多头，等待价格实时向下穿越 ATR 止损线'],
  HOLDING_SHORT: ['空头监控中', '已持有空头，等待价格实时向上穿越 ATR 止损线'],
  REENTRY_LOCKED: ['本 K 线禁止重入', '本根 15 分钟 K 线已经卖出，即使重新上穿也不会买入'],
  BUY_LOCKED: ['本 K 线买入锁定', '本根 15 分钟 K 线已经使用过买入信号'],
  ARMED_FOR_BUY: ['等待向上穿越', '当前价格在 ATR 线下方，下一次实时向上穿越可触发买入'],
  ARMED_FOR_LONG: ['等待做多', '当前价格在 ATR 线下方，向上穿越后建立多头'],
  ARMED_FOR_SHORT: ['等待做空', '当前价格在 ATR 线上方，向下穿越后建立空头'],
  WAITING_FOR_RESET: ['等待重新武装', '当前价格虽高于 ATR，但没有新的下方到上方穿越'],
} as const

const triggerText: Record<string, string> = {
  RESUME_TRADING: '恢复策略执行',
  WAIT_FOR_ATR: '等待 ATR 计算完成',
  NEXT_TICK_FILL: '等待模拟撮合完成',
  PRICE_CROSS_BELOW: '价格实时向下穿越 ATR 线',
  NEXT_BAR_AND_FRESH_UP_CROSS: '进入下一根 K 线，并出现新的向上穿越',
  NEXT_BAR: '等待下一根 15 分钟 K 线',
  PRICE_CROSS_ABOVE: '价格实时向上穿越 ATR 线',
  PRICE_BELOW_THEN_CROSS_ABOVE: '价格先回到 ATR 线下方，再重新上穿',
}

const crossReasonText: Record<string, string> = {
  TRADING_PAUSED: '策略暂停',
  ALREADY_LONG: '已有多头持仓',
  ALREADY_SHORT: '已有空头持仓',
  ORDER_PENDING: '已有待成交订单',
  BUY_LOCKED_THIS_BAR: '本 K 线买入锁',
  REENTRY_LOCKED_THIS_BAR: '卖出后本 K 线禁止重入',
  NO_POSITION: '没有可卖持仓',
  EXIT_LOCKED_THIS_BAR: '本 K 线卖出锁',
}

function DecisionStatus({ runtime }: { runtime: Account['runtime'] }) {
  const decision = runtime.decision
  const strategy = runtime.strategy
  const copy = decisionText[decision.state]
  const tone = decision.state === 'ARMED_FOR_BUY'
    || decision.state === 'ARMED_FOR_LONG'
    || decision.state === 'ARMED_FOR_SHORT'
    || decision.state === 'HOLDING_LONG'
    || decision.state === 'HOLDING_SHORT'
    ? 'ready'
    : decision.state === 'PAUSED'
      ? 'blocked'
      : 'waiting'
  const lastCross = strategy.last_cross
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
        <div><dt>最近 Tick 穿越</dt><dd>{lastCross}</dd></div>
        <div><dt>下一触发条件</dt><dd>{triggerText[decision.next_trigger] ?? decision.next_trigger}</dd></div>
      </dl>
      <div className="decision-gates" aria-label="买入门控">
        <Gate open={decision.trading_enabled} label="策略运行" />
        <Gate open={decision.strategy_ready} label="ATR 就绪" />
        <Gate open={decision.allow_short || !decision.has_position} label={decision.allow_short ? '允许多空反手' : '当前空仓'} />
        <Gate open={!decision.has_pending_order} label="无待成交单" />
        <Gate open={decision.buy_lock_open} label="买入锁开放" />
        <Gate open={decision.reentry_lock_open} label="重入锁开放" />
        <Gate open={decision.signal_confirmation === 'TICK'} label="Tick 实时检测" />
      </div>
    </section>
  )
}

function Gate({ open, label }: { open: boolean; label: string }) {
  return <span className={open ? 'open' : 'closed'}>{open ? <CircleCheck size={13} /> : <CircleX size={13} />}{label}</span>
}

function FillTable({ rows }: { rows: Fill[] }) {
  if (!rows.length) return <div className="empty-table">暂无成交</div>
  return <div className="table-scroll"><table><thead><tr><th>时间</th><th>账户</th><th>方向</th><th>动作</th><th>价格</th><th>数量</th><th>手续费</th><th>数据源</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{time(row.timestamp_ms)}</td><td>{row.account_id.toUpperCase()}</td><td><span className={`side ${row.side.toLowerCase()}`}>{row.side}</span></td><td>{row.position_effect ?? '--'}</td><td>{number(row.price, 4)}</td><td>{number(row.quantity, 3)}</td><td>{number(row.fee, 4)}</td><td>{row.source}</td></tr>)}</tbody></table></div>
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
