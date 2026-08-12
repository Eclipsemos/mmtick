import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BarChart3,
  CalendarDays,
  CheckCircle2,
  Database,
  FileBarChart,
  Play,
  RefreshCw,
  Settings2,
  TriangleAlert,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, ApiError } from './api'
import type {
  ResearchBacktestRequest,
  ResearchCandidate,
  ResearchJob,
} from './types'

const PROFIT = '#078a61'
const LOSS = '#d74750'
const yesterdayUtc = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10)

function parseNumbers(value: string, integer = false) {
  return [...new Set(value.split(',').map((item) => Number(item.trim())).filter((item) => (
    Number.isFinite(item) && item > 0 && (!integer || Number.isInteger(item))
  )))]
}

function percent(value: number | null) {
  if (value === null) return '--'
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

function money(value: number) {
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function compact(value: number) {
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(value)
}

function dateTime(value: number | null) {
  if (!value) return '--'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(value)
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError && typeof error.detail === 'string') return error.detail
  return error instanceof Error ? error.message : '请求失败'
}

function JobProgress({ job }: { job?: ResearchJob }) {
  if (!job) return null
  return (
    <div className={`lab-job ${job.status}`} role="status">
      <div>
        {job.status === 'failed' ? <TriangleAlert size={15} /> : job.status === 'completed' ? <CheckCircle2 size={15} /> : <RefreshCw className="spin" size={15} />}
        <span>{job.stage}</span>
        <strong>{Math.round(job.progress * 100)}%</strong>
      </div>
      <div className="lab-progress"><i style={{ width: `${job.progress * 100}%` }} /></div>
      {(job.error || job.message) && <small>{job.error || job.message}</small>}
    </div>
  )
}

export default function ResearchDashboard() {
  const queryClient = useQueryClient()
  const [updateDate, setUpdateDate] = useState(yesterdayUtc)
  const [startDate, setStartDate] = useState('2026-05-15')
  const [endDate, setEndDate] = useState(yesterdayUtc)
  const [direction, setDirection] = useState<ResearchBacktestRequest['direction']>('long_only')
  const [periodText, setPeriodText] = useState('28,32,35')
  const [multiplierText, setMultiplierText] = useState('2.5,3,3.5')
  const [trendPeriod, setTrendPeriod] = useState(8)
  const [trendEfficiency, setTrendEfficiency] = useState(0.25)
  const [reversalAtr, setReversalAtr] = useState(0)
  const [leverage, setLeverage] = useState(2)
  const [positionFraction, setPositionFraction] = useState(0.625)
  const [feeBps, setFeeBps] = useState(5)
  const [slippageBps, setSlippageBps] = useState(2)
  const [initialCash, setInitialCash] = useState(100000)
  const [profitEnabled, setProfitEnabled] = useState(false)
  const [profitActivation, setProfitActivation] = useState(1.5)
  const [profitTrailing, setProfitTrailing] = useState(1)
  const [reentryEnabled, setReentryEnabled] = useState(false)
  const [reentryAtr, setReentryAtr] = useState(1.4)
  const [updateJobId, setUpdateJobId] = useState('')
  const [backtestJobId, setBacktestJobId] = useState('')
  const [reportId, setReportId] = useState('')
  const [selectedCandidateId, setSelectedCandidateId] = useState('')

  const periods = useMemo(() => parseNumbers(periodText, true), [periodText])
  const multipliers = useMemo(() => parseNumbers(multiplierText), [multiplierText])
  const candidateCount = periods.length * multipliers.length
  const dataStatus = useQuery({ queryKey: ['research-data-status'], queryFn: api.researchDataStatus })
  const reports = useQuery({ queryKey: ['research-reports'], queryFn: api.researchReports })
  const updateJob = useQuery({
    queryKey: ['research-job', updateJobId],
    queryFn: () => api.researchJob(updateJobId),
    enabled: Boolean(updateJobId),
    refetchInterval: (query) => ['completed', 'failed'].includes(query.state.data?.status ?? '') ? false : 1000,
  })
  const backtestJob = useQuery({
    queryKey: ['research-job', backtestJobId],
    queryFn: () => api.researchJob(backtestJobId),
    enabled: Boolean(backtestJobId),
    refetchInterval: (query) => ['completed', 'failed'].includes(query.state.data?.status ?? '') ? false : 1000,
  })
  const report = useQuery({
    queryKey: ['research-report', reportId],
    queryFn: () => api.researchReport(reportId),
    enabled: Boolean(reportId),
  })
  const updateMutation = useMutation({
    mutationFn: api.updateResearchData,
    onSuccess: (job) => setUpdateJobId(job.id),
  })
  const backtestMutation = useMutation({
    mutationFn: api.runResearchBacktest,
    onSuccess: (job) => { setBacktestJobId(job.id); setReportId(''); setSelectedCandidateId('') },
  })

  useEffect(() => {
    if (dataStatus.data?.default_update_date) {
      setUpdateDate(dataStatus.data.default_update_date)
      setEndDate(dataStatus.data.complete_through_date ?? dataStatus.data.default_update_date)
      if (dataStatus.data.earliest_replay_date) {
        setStartDate(dataStatus.data.earliest_replay_date)
      }
    }
  }, [dataStatus.data])
  useEffect(() => {
    if (updateJob.data?.status === 'completed') {
      void queryClient.invalidateQueries({ queryKey: ['research-data-status'] })
    }
  }, [queryClient, updateJob.data?.status])
  useEffect(() => {
    if (backtestJob.data?.status === 'completed' && backtestJob.data.report_id) {
      setReportId(backtestJob.data.report_id)
    }
  }, [backtestJob.data])
  useEffect(() => {
    if (!reportId && !backtestJobId && reports.data?.length) {
      setReportId(reports.data[0].id)
    }
  }, [backtestJobId, reportId, reports.data])
  useEffect(() => {
    if (report.data && !selectedCandidateId) setSelectedCandidateId(report.data.best_candidate_id)
  }, [report.data, selectedCandidateId])

  const selected = report.data?.candidates.find((item) => item.id === selectedCandidateId)
    ?? report.data?.candidates[0]
  const busy = ['queued', 'running'].includes(backtestJob.data?.status ?? '')
  const invalidGrid = candidateCount < 1 || candidateCount > 24

  function submitBacktest(event: FormEvent) {
    event.preventDefault()
    backtestMutation.mutate({
      instrument_id: 'soxl_perp', start_date: startDate, end_date: endDate, direction,
      atr_periods: periods, atr_multipliers: multipliers,
      trend_efficiency_period: trendPeriod,
      minimum_trend_efficiency: trendEfficiency,
      reversal_confirmation_atr: reversalAtr,
      leverage, position_fraction: positionFraction, fee_bps: feeBps,
      slippage_bps: slippageBps, initial_cash: initialCash,
      profit_activation_atr: profitEnabled ? profitActivation : null,
      profit_trailing_atr: profitEnabled ? profitTrailing : null,
      continuation_reentry_atr: reentryEnabled ? reentryAtr : null,
    })
  }

  return (
    <div className="research-dashboard lab-dashboard">
      <section className="lab-data-band" aria-label="历史数据状态">
        <header><Database size={18} /><div><span>MARKET DATA / UTC</span><h2>SOXLUSDT 历史数据</h2></div></header>
        <dl>
          <div><dt>覆盖起点</dt><dd>{dateTime(dataStatus.data?.first_tick_ms ?? null)}</dd></div>
          <div><dt>最新 Tick</dt><dd>{dateTime(dataStatus.data?.last_tick_ms ?? null)}</dd></div>
          <div><dt>最新完整日</dt><dd>{dataStatus.data?.complete_through_date ?? '--'}</dd></div>
          <div><dt>250ms 桶 / 成交</dt><dd>{compact(dataStatus.data?.tick_count ?? 0)} / {compact(dataStatus.data?.raw_trade_count ?? 0)}</dd></div>
        </dl>
        <form onSubmit={(event) => { event.preventDefault(); updateMutation.mutate(updateDate) }}>
          <label>更新到<input type="date" value={updateDate} max={dataStatus.data?.default_update_date ?? yesterdayUtc} onChange={(event) => setUpdateDate(event.target.value)} /></label>
          <button className="control" type="submit" disabled={updateMutation.isPending || ['queued', 'running'].includes(updateJob.data?.status ?? '')}><RefreshCw size={15} />更新完整日</button>
        </form>
      </section>
      <JobProgress job={updateJob.data} />

      <form className="lab-workbench" onSubmit={submitBacktest}>
        <header className="lab-section-head"><Settings2 size={17} /><div><span>ATR GRID</span><h2>批量回测参数</h2></div><strong>{candidateCount} / 24 候选</strong></header>
        <div className="lab-form-grid">
          <fieldset>
            <legend>范围与方向</legend>
            <label>合约<select value="soxl_perp" disabled><option value="soxl_perp">SOXLUSDT 永续合约</option></select></label>
            <div className="lab-field-pair"><label>开始日期<input type="date" value={startDate} min={dataStatus.data?.earliest_replay_date ?? undefined} onChange={(event) => setStartDate(event.target.value)} required /></label><label>结束日期<input type="date" value={endDate} max={dataStatus.data?.complete_through_date ?? dataStatus.data?.default_update_date ?? yesterdayUtc} onChange={(event) => setEndDate(event.target.value)} required /></label></div>
            <div className="lab-label">方向</div>
            <div className="lab-segments" role="group" aria-label="交易方向">
              {([['long_only', '仅做多'], ['short_only', '仅做空'], ['long_short', '多空']] as const).map(([value, label]) => <button key={value} type="button" className={direction === value ? 'active' : ''} onClick={() => setDirection(value)}>{label}</button>)}
            </div>
          </fieldset>
          <fieldset>
            <legend>ATR 网格</legend>
            <label>ATR 周期<input value={periodText} onChange={(event) => setPeriodText(event.target.value)} placeholder="28,32,35" /></label>
            <label>ATR 倍数<input value={multiplierText} onChange={(event) => setMultiplierText(event.target.value)} placeholder="2.5,3,3.5" /></label>
            <div className="lab-field-pair"><label>效率周期<input type="number" min="2" max="100" value={trendPeriod} onChange={(event) => setTrendPeriod(Number(event.target.value))} /></label><label>最低效率<input type="number" min="0" max="1" step="0.05" value={trendEfficiency} onChange={(event) => setTrendEfficiency(Number(event.target.value))} /></label></div>
            <label>反向确认 ATR<input type="number" min="0" step="0.1" value={reversalAtr} onChange={(event) => setReversalAtr(Number(event.target.value))} /></label>
          </fieldset>
          <fieldset>
            <legend>资金与成本</legend>
            <div className="lab-field-pair"><label>杠杆<input type="number" min="1" max="10" value={leverage} onChange={(event) => setLeverage(Number(event.target.value))} /></label><label>仓位比例<input type="number" min="0" max="1" step="0.005" value={positionFraction} onChange={(event) => setPositionFraction(Number(event.target.value))} /></label></div>
            <div className="lab-field-pair"><label>手续费 bps<input type="number" min="0" step="0.1" value={feeBps} onChange={(event) => setFeeBps(Number(event.target.value))} /></label><label>滑点 bps<input type="number" min="0" step="0.1" value={slippageBps} onChange={(event) => setSlippageBps(Number(event.target.value))} /></label></div>
            <label>初始资金 USDT<input type="number" min="0" step="1000" value={initialCash} onChange={(event) => setInitialCash(Number(event.target.value))} /></label>
            <small>目标敞口 {(leverage * positionFraction).toFixed(3)}x</small>
          </fieldset>
          <fieldset>
            <legend>扩展控制</legend>
            <label className="lab-toggle"><input type="checkbox" checked={profitEnabled} onChange={(event) => setProfitEnabled(event.target.checked)} /><span>利润保护</span></label>
            <div className="lab-field-pair"><label>激活 ATR<input type="number" min="0" step="0.1" disabled={!profitEnabled} value={profitActivation} onChange={(event) => setProfitActivation(Number(event.target.value))} /></label><label>跟踪 ATR<input type="number" min="0" step="0.1" disabled={!profitEnabled} value={profitTrailing} onChange={(event) => setProfitTrailing(Number(event.target.value))} /></label></div>
            <label className="lab-toggle"><input type="checkbox" checked={reentryEnabled} onChange={(event) => setReentryEnabled(event.target.checked)} /><span>延续重入</span></label>
            <label>重入阈值 ATR<input type="number" min="0" step="0.1" disabled={!reentryEnabled} value={reentryAtr} onChange={(event) => setReentryAtr(Number(event.target.value))} /></label>
          </fieldset>
        </div>
        <footer>
          <span><CalendarDays size={14} />固定 15m K 线与单 K 线动作锁</span>
          <button className="lab-run" type="submit" disabled={invalidGrid || busy || backtestMutation.isPending}><Play size={16} />运行 {candidateCount} 个候选</button>
        </footer>
      </form>
      {(backtestMutation.error || updateMutation.error) && <div className="lab-error"><TriangleAlert size={15} />{errorMessage(backtestMutation.error || updateMutation.error)}</div>}
      <JobProgress job={backtestJob.data} />

      {report.data && selected && <>
        <section className="lab-ranking">
          <header className="lab-section-head"><FileBarChart size={17} /><div><span>REPORT {report.data.id}</span><h2>候选排名</h2></div><strong>{report.data.metadata.tick_count.toLocaleString()} Ticks</strong></header>
          <div className="lab-table-scroll"><table><thead><tr><th>排名</th><th>ATR</th><th>倍数</th><th>总收益</th><th>最大回撤</th><th>交易</th><th>胜率</th><th>利润因子</th><th>期末净值</th></tr></thead><tbody>{report.data.candidates.map((candidate) => <tr key={candidate.id} className={selected.id === candidate.id ? 'selected' : ''} onClick={() => setSelectedCandidateId(candidate.id)}><td>#{candidate.rank}</td><td>{candidate.parameters.atr_period}</td><td>{candidate.parameters.atr_multiplier}</td><td className={candidate.metrics.net_return >= 0 ? 'gain' : 'loss'}>{percent(candidate.metrics.net_return)}</td><td className="loss">{percent(candidate.metrics.max_drawdown)}</td><td>{candidate.metrics.completed_trades}</td><td>{percent(candidate.metrics.win_rate)}</td><td>{candidate.metrics.profit_factor?.toFixed(2) ?? '--'}</td><td>{money(candidate.metrics.final_equity)}</td></tr>)}</tbody></table></div>
        </section>
        <section className="lab-result-summary">
          <div><span>选中候选</span><strong>ATR({selected.parameters.atr_period}) × {selected.parameters.atr_multiplier}</strong></div>
          <div><span>总收益</span><strong className={selected.metrics.net_return >= 0 ? 'gain' : 'loss'}>{percent(selected.metrics.net_return)}</strong></div>
          <div><span>净利润</span><strong>{money(selected.metrics.net_profit)} USDT</strong></div>
          <div><span>手续费 / 资金费</span><strong>{money(selected.metrics.total_fees)} / {money(selected.metrics.total_funding)}</strong></div>
          <div><span>预热</span><strong>{report.data.metadata.warmup_bars} 根 {report.data.metadata.warmup_interval_minutes ?? 15}m K</strong></div>
          <div><span>实际回放</span><strong>{dateTime(report.data.metadata.start_ms)} - {dateTime(report.data.metadata.end_ms)}</strong></div>
        </section>
        <div className="lab-report-grid">
          <PerformancePanel title="每月收益" rows={selected.monthly} />
          <PerformancePanel title="每日收益" rows={selected.daily} daily />
        </div>
      </>}
    </div>
  )
}

function PerformancePanel({ title, rows, daily = false }: { title: string, rows: ResearchCandidate['daily'], daily?: boolean }) {
  const chartRows = daily && rows.length > 90 ? rows.slice(-90) : rows
  return (
    <section className="lab-performance">
      <header className="lab-section-head"><BarChart3 size={17} /><div><span>CONTINUOUS EQUITY / UTC</span><h2>{title}</h2></div><strong>{rows.length} 期</strong></header>
      <div className="lab-return-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={chartRows} margin={{ top: 12, right: 8, left: -12, bottom: 0 }}><CartesianGrid stroke="#e3e9ed" vertical={false} /><XAxis dataKey="label" minTickGap={28} tick={{ fill: '#687782', fontSize: 9 }} axisLine={false} tickLine={false} /><YAxis tickFormatter={(value) => `${(value * 100).toFixed(0)}%`} tick={{ fill: '#687782', fontSize: 9 }} axisLine={false} tickLine={false} /><Tooltip formatter={(value) => percent(Number(value))} /><Bar dataKey="return" name="收益">{chartRows.map((row) => <Cell key={row.label} fill={row.return < 0 ? LOSS : PROFIT} />)}</Bar></BarChart></ResponsiveContainer></div>
      <div className="lab-period-table"><table><thead><tr><th>{daily ? '日期' : '月份'}</th><th>收益</th><th>净利润</th><th>期末净值</th></tr></thead><tbody>{[...rows].reverse().map((row) => <tr key={row.label}><td>{row.label}</td><td className={row.return >= 0 ? 'gain' : 'loss'}>{percent(row.return)}</td><td>{money(row.net_profit)}</td><td>{money(row.end_equity)}</td></tr>)}</tbody></table></div>
    </section>
  )
}
