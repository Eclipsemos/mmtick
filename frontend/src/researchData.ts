export const researchSnapshot = {
  generatedAt: '2026-08-09',
  dataStart: '2026-05-17 16:00 UTC',
  dataEnd: '2026-08-08 06:50 UTC',
  rawTradeCount: 186_763_958,
  tickBuckets: 10_065_352,
  bars: 8_131,
  fundingRates: 254,
}

export const baselineMetrics = [
  { label: '完整收益', value: '+171.85%', detail: '100,000 → 271,854.63 USDT', tone: 'good' },
  { label: '最大回撤', value: '-26.71%', detail: '1.25x 目标名义敞口', tone: 'bad' },
  { label: '完成交易', value: '124', detail: '市场暴露 37.69%', tone: '' },
  { label: '胜率', value: '42.74%', detail: '平均盈亏比 2.03', tone: '' },
  { label: '利润因子', value: '1.513', detail: '5 / 2 bps 成本模型', tone: 'good' },
  { label: '前 5 笔赢家', value: '103.86%', detail: '占最终净利润', tone: 'warn' },
]

export const monthlyPerformance = [
  { month: '05', label: '2026-05', baseline: 33.32, rolling: 29.29, endEquity: 133_316.13 },
  { month: '06', label: '2026-06', baseline: 38.75, rolling: 42.55, endEquity: 184_975.79 },
  { month: '07', label: '2026-07', baseline: 25.63, rolling: 42.83, endEquity: 232_386.94 },
  { month: '08', label: '2026-08', baseline: 16.98, rolling: 16.98, endEquity: 271_854.63 },
]

export const strategyVsUnderlying = [
  { label: '5 月', strategy: 33.32, underlying: 38.38, strategyEquity: 133_316.13, underlyingEquity: 138_375.38, alpha: -5.06 },
  { label: '6 月', strategy: 84.98, underlying: 62.03, strategyEquity: 184_975.79, underlyingEquity: 162_028.06, alpha: 22.95 },
  { label: '7 月', strategy: 132.39, underlying: -31.93, strategyEquity: 232_386.94, underlyingEquity: 68_067.68, alpha: 164.32 },
  { label: '8 月 8 日', strategy: 171.85, underlying: -16.19, strategyEquity: 271_854.63, underlyingEquity: 83_814.05, alpha: 188.04 },
]

export const augustDaily = [
  { day: '08-01', value: 0, equity: 232_386.94 },
  { day: '08-02', value: -2.3287, equity: 226_975.27 },
  { day: '08-03', value: -2.3973, equity: 221_533.91 },
  { day: '08-04', value: 22.009, equity: 270_291.25 },
  { day: '08-05', value: 0.5662, equity: 271_821.65 },
  { day: '08-06', value: -3.8893, equity: 261_249.77 },
  { day: '08-07', value: 4.0593, equity: 271_854.63 },
  { day: '08-08', value: 0, equity: 271_854.63 },
]

export const lockCandidates = [
  { name: '无锁', short: '无锁', return: 131.12, drawdown: -26.71, status: 'rejected' },
  { name: '固定 15m', short: 'F15', return: 171.85, drawdown: -26.71, status: 'baseline' },
  { name: '滚动 20m', short: 'R20', return: 197.22, drawdown: -26.71, status: 'candidate' },
  { name: '滚动 25m', short: 'R25', return: 203.02, drawdown: -26.71, status: 'candidate' },
  { name: '滚动 28–32m', short: 'R28–32', return: 207.95, drawdown: -23.67, status: 'best' },
  { name: '滚动 35m', short: 'R35', return: 177.65, drawdown: -23.67, status: 'candidate' },
  { name: '滚动 40m', short: 'R40', return: 161.28, drawdown: -23.67, status: 'rejected' },
  { name: '滚动 60m', short: 'R60', return: 54.73, drawdown: -40.62, status: 'rejected' },
]

export const walkForward = [
  { segment: '训练', baseline: 84.98, rolling: 84.31 },
  { segment: '验证', baseline: 23.45, rolling: 35.81 },
  { segment: '留出', baseline: 15.93, rolling: 15.93 },
]

export const atrPeriodNeighborhood = [
  { parameter: 'ATR(28) × 3', train: 71.9, validation: 20.31 },
  { parameter: 'ATR(32) × 3', train: 84.98, validation: 23.45 },
  { parameter: 'ATR(35) × 3', train: 78.22, validation: 23.07 },
]

export const atrMultiplierNeighborhood = [
  { parameter: '2.50', train: -32.13, validation: 23.39 },
  { parameter: '2.75', train: 6.41, validation: 20.31 },
  { parameter: '3.00', train: 84.98, validation: 23.45 },
  { parameter: '3.25', train: 8.07, validation: -3.83 },
  { parameter: '3.50', train: 2.23, validation: -17.12 },
]

export const decisionRegister = [
  { control: '冻结基线', setting: '固定 15m 动作锁', status: '保留', tone: 'active' },
  { control: '动作冷却', setting: '滚动 28–32m', status: '前向观察', tone: 'candidate' },
  { control: '盈利保护', setting: '2.0 / 0.5 ATR', status: '不采用', tone: 'rejected' },
  { control: '延续重入', setting: '0.5 / 1.4 / 2.0 ATR', status: '不采用', tone: 'rejected' },
  { control: '风险预算', setting: '2x × 70%', status: '不批准', tone: 'rejected' },
]
