import {
  Activity,
  BarChart3,
  CandlestickChart,
  Database,
  FlaskConical,
  GitCompareArrows,
  LockKeyhole,
  ShieldCheck,
  Target,
  TrendingUp,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  LineChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  atrMultiplierNeighborhood,
  atrPeriodNeighborhood,
  augustDaily,
  baselineMetrics,
  decisionRegister,
  lockCandidates,
  monthlyPerformance,
  researchSnapshot,
  strategyVsUnderlying,
  walkForward,
} from './researchData'

const COLORS = {
  baseline: '#2878b8',
  candidate: '#078a61',
  warning: '#b47b0a',
  loss: '#d74750',
  grid: '#e3e9ed',
  axis: '#687782',
}

function compact(value: number) {
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(value)
}

function signed(value: number) {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function ResearchTooltip({ active, payload, label }: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="research-tooltip">
      <strong>{label}</strong>
      {payload.map((item) => (
        <span key={item.name}><i style={{ background: item.color }} />{item.name} {signed(item.value)}</span>
      ))}
    </div>
  )
}

export default function ResearchDashboard() {
  return (
    <div className="research-dashboard">
      <section className="research-status-band" aria-label="冻结基线状态">
        <div className="research-status-title">
          <span><LockKeyhole size={14} /> FROZEN BASELINE</span>
          <h2>15m 只做多 · ATR(32) × 3</h2>
          <p>8 根效率窗口 · 阈值 0.25 · 固定 15m 动作锁 · 1.25x 敞口</p>
        </div>
        <dl className="research-status-facts">
          <div><dt>方向</dt><dd>LONG ONLY</dd></div>
          <div><dt>成本</dt><dd>5 / 2 bps</dd></div>
          <div><dt>盈利保护</dt><dd>OFF</dd></div>
          <div><dt>延续重入</dt><dd>OFF</dd></div>
        </dl>
        <div className="research-candidate-state">
          <span><FlaskConical size={14} /> LEADING CANDIDATE</span>
          <strong>成交后滚动 28–32m</strong>
          <small>留出段未形成区分 · 未批准</small>
        </div>
      </section>

      <section className="research-metrics" aria-label="基线回测指标">
        {baselineMetrics.map((metric, index) => {
          const icons = [TrendingUp, Activity, CandlestickChart, Target, ShieldCheck, GitCompareArrows]
          const Icon = icons[index]
          return (
            <article className={`research-metric ${metric.tone}`} key={metric.label}>
              <div><span>{metric.label}</span><Icon size={16} /></div>
              <strong>{metric.value}</strong>
              <small>{metric.detail}</small>
            </article>
          )
        })}
      </section>

      <div className="research-two-column">
        <section className="research-band">
          <header className="research-band-head">
            <div><span>CONTINUOUS ACCOUNT / UTC</span><h3>月度 performance</h3></div>
            <strong>8 月 +16.98%</strong>
          </header>
          <div className="research-chart research-chart-main" aria-label="月度收益图">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={monthlyPerformance} margin={{ top: 18, right: 12, left: -12, bottom: 0 }}>
                <CartesianGrid stroke={COLORS.grid} vertical={false} />
                <XAxis dataKey="label" tick={{ fill: COLORS.axis, fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={(value) => `${value}%`} tick={{ fill: COLORS.axis, fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip content={<ResearchTooltip />} cursor={{ fill: '#f2f5f7' }} />
                <Legend iconType="square" wrapperStyle={{ fontSize: 10 }} />
                <Bar name="当前基线" dataKey="baseline" fill={COLORS.baseline} radius={[2, 2, 0, 0]} />
                <Bar name="滚动 30m" dataKey="rolling" fill={COLORS.candidate} radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="research-equity-strip">
            {monthlyPerformance.map((month) => (
              <div key={month.label}><span>{month.label} 月末</span><strong>{compact(month.endEquity)} USDT</strong></div>
            ))}
          </div>
        </section>

        <section className="research-band">
          <header className="research-band-head">
            <div><span>AUGUST / DAILY MARK-TO-MARKET</span><h3>8 月每日收益</h3></div>
            <strong>271,854.63 USDT</strong>
          </header>
          <div className="research-chart research-chart-daily" aria-label="8 月每日收益图">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={augustDaily} margin={{ top: 18, right: 8, left: -14, bottom: 0 }}>
                <CartesianGrid stroke={COLORS.grid} vertical={false} />
                <XAxis dataKey="day" tick={{ fill: COLORS.axis, fontSize: 9 }} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={(value) => `${value}%`} tick={{ fill: COLORS.axis, fontSize: 9 }} axisLine={false} tickLine={false} />
                <Tooltip content={<ResearchTooltip />} cursor={{ fill: '#f2f5f7' }} />
                <Bar name="日收益" dataKey="value" radius={[2, 2, 0, 0]}>
                  {augustDaily.map((row) => <Cell key={row.day} fill={row.value < 0 ? COLORS.loss : COLORS.candidate} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="research-daily-table">
            {augustDaily.map((row) => (
              <div key={row.day}><span>{row.day}</span><strong className={row.value < 0 ? 'loss' : row.value > 0 ? 'gain' : ''}>{signed(row.value)}</strong><small>{compact(row.equity)}</small></div>
            ))}
          </div>
        </section>
      </div>

      <section className="research-band research-comparison-band">
        <header className="research-band-head">
          <div><span>CONTINUOUS ACCOUNT / SAME STARTING POINT</span><h3>策略 vs 标的累计收益</h3></div>
          <strong>100,000 USDT → 271,854.63</strong>
        </header>
        <div className="research-chart research-chart-comparison" aria-label="策略与标的累计收益对比图">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={strategyVsUnderlying} margin={{ top: 18, right: 18, left: -8, bottom: 0 }}>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxis dataKey="label" tick={{ fill: COLORS.axis, fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={(value) => `${value}%`} tick={{ fill: COLORS.axis, fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip content={<ResearchTooltip />} cursor={{ stroke: COLORS.grid }} />
              <Legend iconType="plainline" wrapperStyle={{ fontSize: 10 }} />
              <Line type="monotone" name="当前策略" dataKey="strategy" stroke={COLORS.candidate} strokeWidth={3} dot={{ r: 4 }} />
              <Line type="monotone" name="SOXLUSDT 1x" dataKey="underlying" stroke={COLORS.loss} strokeWidth={2} dot={{ r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="research-comparison-table">
          {strategyVsUnderlying.map((row) => (
            <div key={row.label}>
              <span>{row.label}</span>
              <strong className="gain">策略 {signed(row.strategy)}</strong>
              <strong className={row.underlying >= 0 ? 'gain' : 'loss'}>标的 {signed(row.underlying)}</strong>
              <small>超额 {signed(row.alpha)} · {compact(row.strategyEquity)} vs {compact(row.underlyingEquity)} USDT</small>
            </div>
          ))}
        </div>
      </section>

      <section className="research-band research-lock-band">
        <header className="research-band-head">
          <div><span>ACTION LOCK / FULL HISTORY</span><h3>动作锁候选比较</h3></div>
          <div className="research-head-badges"><span className="active">F15 基线</span><span className="candidate">R28–32 候选</span></div>
        </header>
        <div className="research-chart research-chart-locks" aria-label="动作锁候选收益和回撤图">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={lockCandidates} margin={{ top: 20, right: 28, left: -8, bottom: 0 }}>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxis dataKey="short" tick={{ fill: COLORS.axis, fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis yAxisId="return" tickFormatter={(value) => `${value}%`} tick={{ fill: COLORS.axis, fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis yAxisId="drawdown" orientation="right" domain={[-45, 0]} tickFormatter={(value) => `${value}%`} tick={{ fill: COLORS.axis, fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip content={<ResearchTooltip />} cursor={{ fill: '#f2f5f7' }} />
              <Legend iconType="square" wrapperStyle={{ fontSize: 10 }} />
              <Bar yAxisId="return" name="完整收益" dataKey="return" radius={[2, 2, 0, 0]}>
                {lockCandidates.map((row) => <Cell key={row.name} fill={row.status === 'best' ? COLORS.candidate : row.status === 'baseline' ? COLORS.baseline : '#8da0ac'} />)}
              </Bar>
              <Line yAxisId="drawdown" name="最大回撤" dataKey="drawdown" stroke={COLORS.loss} strokeWidth={2} dot={{ r: 3, fill: '#fff' }} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <div className="research-lock-table">
          <table>
            <thead><tr><th>版本</th><th>收益</th><th>回撤</th><th>研究状态</th></tr></thead>
            <tbody>{lockCandidates.map((row) => (
              <tr key={row.name}><td>{row.name}</td><td className="gain">{signed(row.return)}</td><td className="loss">{signed(row.drawdown)}</td><td><ResearchStatus status={row.status} /></td></tr>
            ))}</tbody>
          </table>
        </div>
      </section>

      <div className="research-two-column research-evidence-grid">
        <section className="research-band">
          <header className="research-band-head"><div><span>WALK-FORWARD</span><h3>分段验证</h3></div><strong>留出无区分</strong></header>
          <div className="research-chart research-chart-compact" aria-label="walk-forward 收益图">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={walkForward} margin={{ top: 18, right: 12, left: -12, bottom: 0 }}>
                <CartesianGrid stroke={COLORS.grid} vertical={false} />
                <XAxis dataKey="segment" tick={{ fill: COLORS.axis, fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={(value) => `${value}%`} tick={{ fill: COLORS.axis, fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip content={<ResearchTooltip />} cursor={{ fill: '#f2f5f7' }} />
                <Legend iconType="square" wrapperStyle={{ fontSize: 10 }} />
                <Bar name="固定 15m" dataKey="baseline" fill={COLORS.baseline} radius={[2, 2, 0, 0]} />
                <Bar name="滚动 30m" dataKey="rolling" fill={COLORS.candidate} radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="research-band-note">滚动 28–32m 在训练、验证和留出段产生同一条候选路径；留出结果与当前基线完全相同。</p>
        </section>

        <section className="research-band">
          <header className="research-band-head"><div><span>DECISION REGISTER</span><h3>研究决策</h3></div><strong>5 controls</strong></header>
          <div className="research-decisions">
            {decisionRegister.map((row) => (
              <div key={row.control}><span>{row.control}</span><strong>{row.setting}</strong><i className={row.tone}>{row.status}</i></div>
            ))}
          </div>
        </section>
      </div>

      <div className="research-two-column research-parameter-grid">
        <ParameterTable title="ATR 周期邻域" kicker="PERIOD NEIGHBORHOOD" rows={atrPeriodNeighborhood} />
        <ParameterTable title="ATR 倍数断崖" kicker="MULTIPLIER SENSITIVITY" rows={atrMultiplierNeighborhood} />
      </div>

      <section className="research-data-band" aria-label="研究数据覆盖">
        <div><Database size={18} /><span>DATA COVERAGE</span><strong>{researchSnapshot.dataStart} → {researchSnapshot.dataEnd}</strong></div>
        <dl>
          <div><dt>250ms 成交桶</dt><dd>{compact(researchSnapshot.tickBuckets)}</dd></div>
          <div><dt>底层成交 ID</dt><dd>{compact(researchSnapshot.rawTradeCount)}</dd></div>
          <div><dt>15m K 线</dt><dd>{researchSnapshot.bars.toLocaleString()}</dd></div>
          <div><dt>资金费率</dt><dd>{researchSnapshot.fundingRates}</dd></div>
        </dl>
        <div className="research-forward-gate"><BarChart3 size={17} /><span>NEXT REVIEW</span><strong>3 个完整月 · 100 笔前向交易</strong></div>
      </section>
    </div>
  )
}

function ResearchStatus({ status }: { status: string }) {
  const label = status === 'best' ? '最佳候选' : status === 'baseline' ? '冻结基线' : status === 'candidate' ? '邻域候选' : '排除'
  return <span className={`research-status-pill ${status}`}>{label}</span>
}

function ParameterTable({ title, kicker, rows }: {
  title: string
  kicker: string
  rows: Array<{ parameter: string; train: number; validation: number }>
}) {
  return (
    <section className="research-band research-parameter-band">
      <header className="research-band-head"><div><span>{kicker}</span><h3>{title}</h3></div><strong>{rows.length} points</strong></header>
      <table>
        <thead><tr><th>参数</th><th>训练</th><th>验证</th><th>一致性</th></tr></thead>
        <tbody>{rows.map((row) => {
          const stable = row.train > 0 && row.validation > 0
          return <tr key={row.parameter}><td>{row.parameter}</td><td className={row.train >= 0 ? 'gain' : 'loss'}>{signed(row.train)}</td><td className={row.validation >= 0 ? 'gain' : 'loss'}>{signed(row.validation)}</td><td><span className={`parameter-state ${stable ? 'stable' : 'unstable'}`}>{stable ? '正 / 正' : '断裂'}</span></td></tr>
        })}</tbody>
      </table>
    </section>
  )
}
