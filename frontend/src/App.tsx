import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ArrowDownToLine,
  CircleArrowDown,
  CircleArrowUp,
  CirclePause,
  CirclePlay,
  Clock3,
  Gauge,
  ListOrdered,
  Radio,
  RefreshCw,
  ScrollText,
  TrendingDown,
  TrendingUp,
  WalletCards,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import {
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
import type { Account, EquityPoint, EventItem, Fill, Order } from './types'

type View = 'monitor' | 'orders' | 'events'

type PricePoint = {
  timestamp: number
  price: number
  trailingStop: number | null
  change: number
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

function number(value: string | number | null, digits = 3) {
  if (value === null) return '--'
  return Number(value).toLocaleString('en-US', { maximumFractionDigits: digits })
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
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview })
  const equity = useQuery({ queryKey: ['equity', 'soxlb'], queryFn: () => api.equity('soxlb') })
  const fills = useQuery({ queryKey: ['fills', 'soxlb'], queryFn: () => api.fills('soxlb') })
  const orders = useQuery({ queryKey: ['orders', 'soxlb'], queryFn: () => api.orders('soxlb') })
  const events = useQuery({ queryKey: ['events', 'soxlb'], queryFn: () => api.events('soxlb') })
  const control = useMutation({
    mutationFn: api.control,
    onSuccess: () => client.invalidateQueries({ queryKey: ['overview'] }),
  })

  const account = overview.data?.accounts[0]
  const liveCount = overview.data?.instruments.filter((item) => item.status === 'LIVE').length ?? 0
  const instrumentCount = overview.data?.instruments.length ?? 1

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
          <button className={view === 'events' ? 'active' : ''} onClick={() => setView('events')}>
            <ScrollText size={16} />事件
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
            <div className="kicker">SHORT-HORIZON EXECUTION / ATR TICK V1</div>
            <h1>实时模拟盘</h1>
          </div>
          <div className="scope-chips">
            <span>SOXLBUSDT</span><span>15m</span><span>LONG ONLY</span>
          </div>
        </section>

        {overview.isError ? (
          <div className="error-state">
            API 无法连接
            <button onClick={() => overview.refetch()}><RefreshCw size={15} />重试</button>
          </div>
        ) : view === 'monitor' ? (
          <Monitor account={account} equity={equity.data ?? []} fills={fills.data ?? []} />
        ) : view === 'orders' ? (
          <Orders rows={orders.data ?? []} />
        ) : (
          <Events rows={events.data ?? []} />
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
}: {
  account?: Account
  equity: EquityPoint[]
  fills: Fill[]
}) {
  const accountFills = useMemo(
    () => fills.filter((fill) => fill.account_id === 'soxlb'),
    [fills],
  )
  const priceChart = useMemo(() => buildPriceChart(equity), [equity])
  const tradeMarkers = useMemo(() => buildTradeMarkers(accountFills), [accountFills])
  const completedTrades = useMemo(() => buildCompletedTrades(accountFills), [accountFills])
  const equityChart = useMemo(
    () => equity.map((point) => ({ timestamp: point.timestamp_ms, equity: Number(point.equity) })),
    [equity],
  )
  if (!account) return <div className="loading"><Activity size={20} />正在初始化账户</div>
  const runtime = account.runtime
  const strategy = runtime.strategy
  const positive = Number(account.total_pnl) >= 0
  const periodReturn = priceChart.length > 1 ? priceChart.at(-1)!.change : 0

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
        <Metric label="当前持仓" value={number(account.quantity)} sub={`均价 ${money(account.average_price, account.currency)}`} icon={<Activity />} />
        <Metric label="最大回撤" value={percent(account.max_drawdown)} sub={`${account.round_trips} 次完整交易`} tone="bad" icon={<TrendingDown />} />
      </section>

      <section className="workspace-grid">
        <div className="panel price-panel">
          <div className="panel-head price-head">
            <div><span>PRICE / ATR TRAILING STOP</span><h3>价格与交易信号</h3></div>
            <div className="chart-summary">
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
                    fontSize={10}
                    tickFormatter={(value) => time(Number(value))}
                  />
                  <YAxis
                    domain={['auto', 'auto']}
                    stroke="#66727d"
                    tickLine={false}
                    axisLine={false}
                    width={58}
                    fontSize={10}
                    tickFormatter={(value) => Number(value).toFixed(2)}
                  />
                  <Tooltip content={<PriceTooltip currency={account.currency} />} />
                  {completedTrades.map((trade) => {
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
                        fillOpacity={0.16}
                        stroke={color}
                        strokeOpacity={0.8}
                        strokeDasharray="4 3"
                        ifOverflow="extendDomain"
                        label={{
                          value: `${money(trade.netPnl, account.currency)} · ${percent(trade.returnPercent)}`,
                          position: 'center',
                          fill: color,
                          fontSize: 11,
                        }}
                      />
                    )
                  })}
                  <Line dataKey="price" name="价格" type="monotone" stroke="#e7ecef" strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Line dataKey="trailingStop" name="ATR 止损线" type="stepAfter" stroke="#e8bd58" strokeWidth={1.8} strokeDasharray="6 4" dot={false} connectNulls isAnimationActive={false} />
                  <Scatter
                    data={tradeMarkers}
                    dataKey="price"
                    name="成交"
                    shape={(props: unknown) => <TradeMarker {...(props as TradeMarkerProps)} />}
                    isAnimationActive={false}
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
            <div><dt>买入锁</dt><dd>{strategy.bought_this_bar ? 'LOCKED' : 'OPEN'}</dd></div>
            <div><dt>空仓锁</dt><dd>{strategy.flattened_this_bar ? 'LOCKED' : 'OPEN'}</dd></div>
          </dl>
          <div className="strategy-foot"><span>15m</span><span>100% equity</span><span>next tick fill</span></div>
        </div>
      </section>

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

function buildPriceChart(equity: EquityPoint[]): PricePoint[] {
  const valid = equity.filter((point) => Number.isFinite(Number(point.price)))
  const firstPrice = valid.length ? Number(valid[0].price) : 0
  return valid.map((point) => ({
    timestamp: point.timestamp_ms,
    price: Number(point.price),
    trailingStop: point.trailing_stop === null ? null : Number(point.trailing_stop),
    change: firstPrice ? Number(point.price) / firstPrice - 1 : 0,
  }))
}

function buildTradeMarkers(fills: Fill[]): TradePoint[] {
  return fills.map((fill) => ({
    timestamp: fill.timestamp_ms,
    price: Number(fill.price),
    side: fill.side as 'BUY' | 'SELL',
  }))
}

function buildCompletedTrades(fills: Fill[]): CompletedTrade[] {
  const ordered = [...fills].sort((left, right) => left.timestamp_ms - right.timestamp_ms)
  const trades: CompletedTrade[] = []
  let entry: Fill | null = null

  for (const fill of ordered) {
    const quantity = Number(fill.quantity)
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
    const netPnl = (exitUnitProceeds - entryUnitCost) * matchedQuantity
    trades.push({
      entryTimestamp: entry.timestamp_ms,
      exitTimestamp: fill.timestamp_ms,
      entryPrice: Number(entry.price),
      exitPrice: Number(fill.price),
      netPnl,
      returnPercent: exitUnitProceeds / entryUnitCost - 1,
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
}: {
  active?: boolean
  payload?: Array<{ payload: PricePoint }>
  label?: number
  currency: string
}) {
  const point = payload?.[0]?.payload
  if (!active || !point) return null
  return (
    <div className="price-tooltip">
      <time>{time(label ?? point.timestamp)}</time>
      <div><span>价格</span><strong>{money(point.price, currency)}</strong></div>
      <div><span>ATR 止损线</span><strong>{money(point.trailingStop, currency)}</strong></div>
      <div><span>区间涨跌</span><strong className={point.change >= 0 ? 'good-text' : 'bad-text'}>{percent(point.change)}</strong></div>
    </div>
  )
}

function Metric({ label, value, sub, tone = '', icon }: { label: string; value: string; sub: string; tone?: string; icon: React.ReactNode }) {
  return <div className={`metric ${tone}`}><div className="metric-top"><span>{label}</span>{icon}</div><strong>{value}</strong><small>{sub}</small></div>
}

function FillTable({ rows }: { rows: Fill[] }) {
  if (!rows.length) return <div className="empty-table">暂无成交</div>
  return <div className="table-scroll"><table><thead><tr><th>时间</th><th>账户</th><th>方向</th><th>价格</th><th>数量</th><th>手续费</th><th>数据源</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{time(row.timestamp_ms)}</td><td>{row.account_id.toUpperCase()}</td><td><span className={`side ${row.side.toLowerCase()}`}>{row.side}</span></td><td>{number(row.price, 4)}</td><td>{number(row.quantity, 3)}</td><td>{number(row.fee, 4)}</td><td>{row.source}</td></tr>)}</tbody></table></div>
}

function Orders({ rows }: { rows: Order[] }) {
  return <section className="panel full-table"><div className="panel-head"><div><span>ORDER LEDGER</span><h3>全部订单</h3></div><span>{rows.length} records</span></div>{!rows.length ? <div className="empty-table">暂无订单</div> : <div className="table-scroll"><table><thead><tr><th>提交时间</th><th>账户</th><th>方向</th><th>状态</th><th>信号价</th><th>成交价</th><th>ATR</th><th>原因</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{time(row.submitted_at_ms)}</td><td>{row.account_id.toUpperCase()}</td><td><span className={`side ${row.side.toLowerCase()}`}>{row.side}</span></td><td>{row.status}</td><td>{number(row.signal_price, 4)}</td><td>{number(row.fill_price, 4)}</td><td>{number(row.atr, 4)}</td><td>{row.reason}</td></tr>)}</tbody></table></div>}</section>
}

function Events({ rows }: { rows: EventItem[] }) {
  return <section className="panel full-table"><div className="panel-head"><div><span>RUNTIME AUDIT</span><h3>事件日志</h3></div><span>{rows.length} records</span></div>{!rows.length ? <div className="empty-table">暂无事件</div> : <div className="event-list">{rows.map((row) => <div className="event-row" key={row.id}><span className={`event-level ${row.level.toLowerCase()}`}>{row.level}</span><time>{time(row.timestamp_ms)}</time><strong>{row.account_id.toUpperCase()}</strong><span>{row.event_type}</span><p>{row.message}</p></div>)}</div>}</section>
}

export default App
