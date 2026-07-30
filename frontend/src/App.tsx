import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ArrowDownToLine,
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
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from './api'
import type { Account, EquityPoint, EventItem, Fill, Order } from './types'

type View = 'monitor' | 'orders' | 'events'

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
  const [accountId, setAccountId] = useState('soxlb')
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview })
  const equity = useQuery({
    queryKey: ['equity', accountId],
    queryFn: () => api.equity(accountId),
  })
  const fills = useQuery({ queryKey: ['fills'], queryFn: api.fills })
  const orders = useQuery({ queryKey: ['orders'], queryFn: api.orders })
  const events = useQuery({ queryKey: ['events'], queryFn: api.events })
  const control = useMutation({
    mutationFn: api.control,
    onSuccess: () => client.invalidateQueries({ queryKey: ['overview'] }),
  })

  const selected = overview.data?.accounts.find((item) => item.id === accountId)
  const liveCount = overview.data?.instruments.filter((item) => item.status === 'LIVE').length ?? 0

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mastermind">mastermind</span><span className="brand-colon">:</span><span>tick</span>
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
          <span className={`feed-state ${liveCount ? 'live' : ''}`}><i />{liveCount}/2 LIVE</span>
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
          <div className="account-switch" role="group" aria-label="选择模拟账户">
            {overview.data?.accounts.map((account) => (
              <button
                key={account.id}
                className={accountId === account.id ? 'active' : ''}
                onClick={() => setAccountId(account.id)}
              >
                {account.display_symbol}
              </button>
            ))}
          </div>
        </section>

        {overview.isError ? (
          <div className="error-state">API 无法连接 <button onClick={() => overview.refetch()}><RefreshCw size={15} />重试</button></div>
        ) : view === 'monitor' ? (
          <Monitor account={selected} equity={equity.data ?? []} fills={fills.data ?? []} tracking={overview.data?.tracking ?? null} />
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
  tracking,
}: {
  account?: Account
  equity: EquityPoint[]
  fills: Fill[]
  tracking: { soxl_price: string; soxlb_price: string; premium: number } | null
}) {
  const chart = useMemo(
    () => equity.map((point) => ({ time: time(point.timestamp_ms), equity: Number(point.equity) })),
    [equity],
  )
  if (!account) return <div className="loading"><Activity size={20} />正在初始化账户</div>
  const runtime = account.runtime
  const strategy = runtime.strategy
  const positive = Number(account.total_pnl) >= 0
  const accountFills = fills.filter((fill) => fill.account_id === account.id)

  return (
    <>
      <section className="instrument-band">
        <div className="symbol-block">
          <div className="symbol-line"><h2>{account.display_symbol}</h2><span>{runtime.asset_type.replace('_', ' ')}</span></div>
          <p>{runtime.name}</p>
        </div>
        <div className="quote-block">
          <strong>{money(account.last_price, account.currency)}</strong>
          <span><Clock3 size={13} />{time(account.last_snapshot_ms)}</span>
        </div>
        <div className="source-block">
          <span className={`runtime ${runtime.status.toLowerCase()}`}><Radio size={14} />{runtime.status}</span>
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
        <div className="panel equity-panel">
          <div className="panel-head"><div><span>ACCOUNT EQUITY</span><h3>净值曲线</h3></div><strong>{money(account.equity, account.currency)}</strong></div>
          <div className="chart-wrap">
            {chart.length > 1 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chart} margin={{ top: 12, right: 10, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="#20272d" vertical={false} />
                  <XAxis dataKey="time" stroke="#66727d" tickLine={false} axisLine={false} minTickGap={70} fontSize={10} />
                  <YAxis domain={['auto', 'auto']} stroke="#66727d" tickLine={false} axisLine={false} width={72} fontSize={10} tickFormatter={(v) => Number(v).toLocaleString()} />
                  <Tooltip contentStyle={{ background: '#12171b', border: '1px solid #293139', borderRadius: 4 }} formatter={(v) => money(Number(v), account.currency)} />
                  <Line dataKey="equity" type="monotone" stroke="#3fd6a1" strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : <div className="empty-chart">等待下一条净值快照</div>}
          </div>
        </div>

        <div className="panel strategy-panel">
          <div className="panel-head"><div><span>STRATEGY STATE</span><h3>ATR 移动线</h3></div><span className={`relation ${strategy.relation}`}>{strategy.relation.toUpperCase()}</span></div>
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
          <div className="panel-head"><div><span>EXECUTIONS</span><h3>最近成交</h3></div><a className="export" href={`/api/fills.csv?account_id=${account.id}`}><ArrowDownToLine size={15} />CSV</a></div>
          <FillTable rows={accountFills.slice(0, 8)} />
        </div>
        <div className="panel tracking-panel">
          <div className="panel-head"><div><span>UNDERLYING LINK</span><h3>SOXLB / SOXL</h3></div></div>
          <div className="tracking-prices"><div><span>Token</span><strong>{tracking ? money(tracking.soxlb_price) : '--'}</strong></div><div><span>ETF</span><strong>{tracking ? money(tracking.soxl_price) : '--'}</strong></div></div>
          <div className="premium-row"><span>名义溢价 / 折价</span><strong className={tracking && tracking.premium >= 0 ? 'good-text' : 'bad-text'}>{tracking ? percent(tracking.premium) : '--'}</strong></div>
          <div className="data-note">报价时间可能不同，偏离值仅用于监控。</div>
        </div>
      </section>
    </>
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

