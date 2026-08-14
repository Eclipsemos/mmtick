import { expect, test } from '@playwright/test'

const researchPresets = [
  {
    instrument_id: 'soxl_perp', symbol: 'SOXLUSDT', display_symbol: 'SOXL/USDT PERP',
    name: 'SOXL USD-M Perpetual', history_start_date: '2026-05-15', direction: 'long_only',
    atr_periods: [28, 32, 35], atr_multipliers: [2.5, 3, 3.5],
    trend_efficiency_period: 8, minimum_trend_efficiency: 0.25,
    reversal_confirmation_atr: 0, leverage: 2, position_fraction: 0.625,
    fee_bps: 5, slippage_bps: 2, status: 'researched_candidate_grid',
  },
  {
    instrument_id: 'btc_perp', symbol: 'BTCUSDT', display_symbol: 'BTC/USDT PERP',
    name: 'Bitcoin USD-M Perpetual', history_start_date: '2024-01-01', direction: 'long_short',
    atr_periods: [14, 21, 28], atr_multipliers: [2, 2.5, 3],
    trend_efficiency_period: 8, minimum_trend_efficiency: 0.25,
    reversal_confirmation_atr: 0, leverage: 1, position_fraction: 1,
    fee_bps: 5, slippage_bps: 2, status: 'baseline_rejected_validation',
  },
  {
    instrument_id: 'eth_perp', symbol: 'ETHUSDT', display_symbol: 'ETH/USDT PERP',
    name: 'Ethereum USD-M Perpetual', history_start_date: '2026-05-01', direction: 'long_short',
    atr_periods: [14, 21, 28], atr_multipliers: [2, 2.5, 3],
    trend_efficiency_period: 8, minimum_trend_efficiency: 0.25,
    reversal_confirmation_atr: 0, leverage: 1, position_fraction: 1,
    fee_bps: 5, slippage_bps: 2, status: 'baseline_unoptimized',
  },
]

test.beforeEach(async ({ page }) => {
  await page.route('**/api/research/presets', (route) => route.fulfill({ json: researchPresets }))
  await page.route('**/api/research/reports?*', (route) => route.fulfill({ json: [] }))
})

test('research branch runs the ATR workbench and renders its report', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await page.route('**/api/research/data-status?*', (route) => route.fulfill({ json: {
    instrument_id: 'soxl_perp', symbol: 'SOXLUSDT', first_tick_ms: 1778803200000,
    last_tick_ms: 1786406399000, tick_count: 10_000_000, raw_trade_count: 81_000_000,
    bar_count: 8448, funding_count: 265, complete_through_date: '2026-08-10',
    default_update_date: '2026-08-10', database_path: 'data/paper.db',
  } }))
  await page.route('**/api/research/data-update', (route) => route.fulfill({ status: 202, json: {
    id: 'update-1', kind: 'data_update', status: 'queued', stage: '等待研究任务队列',
    progress: 0, created_at: '', started_at: null, completed_at: null, report_id: null,
    error: null, message: null, request: {},
  } }))
  await page.route('**/api/research/backtests', (route) => route.fulfill({ status: 202, json: {
    id: 'backtest-1', kind: 'backtest', status: 'queued', stage: '等待研究任务队列',
    progress: 0, created_at: '', started_at: null, completed_at: null, report_id: null,
    error: null, message: null, request: {},
  } }))
  await page.route('**/api/research/jobs/update-1', (route) => route.fulfill({ json: {
    id: 'update-1', kind: 'data_update', status: 'completed', stage: '数据已是最新完整日',
    progress: 1, created_at: '', started_at: '', completed_at: '', report_id: null,
    error: null, message: '无需更新', request: {},
  } }))
  await page.route('**/api/research/jobs/backtest-1', (route) => route.fulfill({ json: {
    id: 'backtest-1', kind: 'backtest', status: 'completed', stage: '回测报告已生成',
    progress: 1, created_at: '', started_at: '', completed_at: '', report_id: 'atr-test',
    error: null, message: '完成 9 个 ATR 候选', request: {},
  } }))
  await page.route('**/api/research/reports/atr-test', (route) => route.fulfill({ json: {
    id: 'atr-test', generated_at: '2026-08-11T00:00:00Z', instrument_id: 'soxl_perp',
    symbol: 'SOXLUSDT', best_candidate_id: 'candidate-1',
    request: { instrument_id: 'soxl_perp', start_date: '2026-05-15', end_date: '2026-08-10', direction: 'long_only' },
    metadata: { start_ms: 1, end_ms: 2, tick_count: 10_000_000, raw_trade_count: 81_000_000, warmup_bars: 200, target_exposure: 1.25 },
    candidates: [{
      id: 'candidate-1', rank: 1,
      parameters: { atr_period: 32, atr_multiplier: 3, profit_activation_atr: null, profit_trailing_atr: null, continuation_reentry_atr: null },
      metrics: { initial_equity: 100000, final_equity: 125000, net_profit: 25000, net_return: .25, completed_trades: 18, win_rate: .72, profit_factor: 2.1, max_drawdown: -.12, total_fees: 500, total_funding: 20, ending_position: 'LONG' },
      monthly: [{ label: '2026-08', start_equity: 100000, end_equity: 125000, net_profit: 25000, return: .25 }],
      daily: [{ label: '2026-08-10', timestamp_ms: 2, start_equity: 120000, end_equity: 125000, net_profit: 5000, return: .0416667 }],
    }],
  } }))

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '多品种 ATR 回测平台' })).toBeVisible()
  await expect(page.getByText('NO TRADING')).toBeVisible()
  await expect(page.getByRole('region', { name: '历史数据状态' })).toContainText('2026-08-10')
  await expect(page.getByLabel('合约')).toHaveValue('soxl_perp')
  await expect(page.getByText('9 / 24 候选')).toBeVisible()

  await page.getByRole('button', { name: '更新完整日' }).click()
  await expect(page.getByText('数据已是最新完整日')).toBeVisible()
  await page.getByRole('button', { name: '运行 9 个候选' }).click()
  await expect(page.getByRole('heading', { name: '候选排名' })).toBeVisible()
  await expect(page.getByText('ATR(32) × 3')).toBeVisible()
  await expect(page.getByRole('heading', { name: '每月收益' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '每日收益' })).toBeVisible()
  await expect(page.getByText('+25.00%').first()).toBeVisible()

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(hasHorizontalOverflow).toBe(false)
  expect(consoleErrors).toEqual([])
})

test('contract selector applies unoptimized BTC and ETH research presets', async ({ page }) => {
  await page.route('**/api/research/data-status?*', async (route) => {
    const instrumentId = new URL(route.request().url()).searchParams.get('instrument_id')
    const symbol = instrumentId === 'eth_perp' ? 'ETHUSDT' : instrumentId === 'btc_perp' ? 'BTCUSDT' : 'SOXLUSDT'
    await route.fulfill({ json: {
      instrument_id: instrumentId, symbol, first_tick_ms: null, last_tick_ms: null,
      tick_count: 0, raw_trade_count: 0, bar_count: 0, funding_count: 0,
      complete_through_date: null, earliest_replay_ms: null, earliest_replay_date: null,
      default_update_date: '2026-08-10', database_path: 'data/paper.db',
    } })
  })

  await page.goto('/')
  const contract = page.getByLabel('合约')
  await expect(contract.locator('option')).toHaveCount(3)
  await contract.selectOption('btc_perp')

  await expect(page.getByRole('heading', { name: 'BTCUSDT 历史数据' })).toBeVisible()
  await expect(page.getByText('研究基线预设，尚未优化')).toBeVisible()
  await expect(page.getByRole('button', { name: '多空' })).toHaveClass(/active/)
  await expect(page.getByLabel('ATR 周期')).toHaveValue('14,21,28')
  await expect(page.getByLabel('ATR 倍数')).toHaveValue('2,2.5,3')
  await expect(page.getByLabel('杠杆')).toHaveValue('1')
  await expect(page.getByLabel('仓位比例')).toHaveValue('1')

  await contract.selectOption('eth_perp')
  await expect(page.getByRole('heading', { name: 'ETHUSDT 历史数据' })).toBeVisible()
})

test('research workbench fits a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/research/data-status?*', (route) => route.fulfill({ json: {
    instrument_id: 'soxl_perp', symbol: 'SOXLUSDT', first_tick_ms: 1778803200000,
    last_tick_ms: 1786406399000, tick_count: 10_000_000, raw_trade_count: 81_000_000,
    bar_count: 8448, funding_count: 265, complete_through_date: '2026-08-10',
    default_update_date: '2026-08-10', database_path: 'data/paper.db',
  } }))

  await page.goto('/')
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(hasHorizontalOverflow).toBe(false)
  await expect(page.getByRole('button', { name: '运行 9 个候选' })).toBeVisible()
})

test('historical replay and data views remain available without trading controls', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '回放', exact: true }).click()
  await expect(page.getByRole('heading', { name: '历史策略回放' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'SOXL 合约实盘就绪状态' })).toHaveCount(0)
  await expect(page.getByText('SOXL/USDT PERP', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('LONG ONLY', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'SOXL/USDT PERP LONG ONLY', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'SOXL/USDT PERP LONG ONLY' })).toBeVisible()
  await expect(page.getByText('LONG ONLY', { exact: true })).toBeVisible()
  await expect(page.getByText('2x isolated')).toHaveCount(0)
  await expect(page.getByText('当前资金费')).toBeVisible()
  await page.getByRole('button', { name: 'SOXL/USDT PERP', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'SOXL/USDT PERP' })).toBeVisible()
  await expect(page.getByText('LONG ONLY', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'SOXL/USDT PERP', exact: true }).click()
  await expect(page.getByText('账户净值')).toBeVisible()
  await expect(page.getByText('夏普率')).toBeVisible()
  await expect(page.getByText('轮次胜率')).toBeVisible()
  await expect(page.getByText('交易决策状态')).toBeVisible()
  await expect(page.getByText('下一触发条件')).toBeVisible()
  await expect(page.getByText('ATR 实时状态')).toBeVisible()
  await expect(page.getByText('ATR 周期')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '显示策略参数' })).toHaveCount(0)
  await expect(page.getByText('信号检测')).toBeVisible()
  await expect(page.getByText('每个成交 Tick')).toBeVisible()
  await expect(page.getByText('成交时点')).toBeVisible()
  await expect(page.getByText('下一成交 Tick')).toBeVisible()
  await expect(page.getByText('趋势效率比', { exact: true })).toBeVisible()
  await expect(page.getByText('趋势过滤', { exact: true })).toBeVisible()
  await expect(page.getByText('本 K 线动作锁', { exact: true })).toBeVisible()
  await expect(page.getByText('反向确认', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '放大价格图' })).toBeVisible()
  await expect(page.getByRole('button', { name: '缩小价格图' })).toBeVisible()
  await expect(page.locator('.recharts-brush')).toBeVisible()
  await expect(page.getByRole('heading', { name: '官方 15 分钟 K线' })).toBeVisible()
  await expect(page.getByTestId('official-kline-chart')).toBeVisible()
  await expect(page.locator('.kline-candle').first()).toBeVisible()
  await expect(page.getByRole('button', { name: '放大K线' })).toBeVisible()
  await expect(page.getByRole('button', { name: '缩小K线' })).toBeVisible()

  await page.getByRole('button', { name: /回测明细/ }).click()
  await expect(page.getByRole('heading', { name: '回测交易明细' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '全部订单' })).toBeVisible()
  await page.getByRole('button', { name: '绩效', exact: true }).click()
  await expect(page.getByRole('heading', { name: '历史绩效' })).toBeVisible()
  await expect(page.getByText('近 30 日收益')).toBeVisible()
  await expect(page.getByRole('heading', { name: '最近 30 天收益日历' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '每周收益' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '每月收益' })).toBeVisible()
  await expect(page.getByText('年化收益')).toBeVisible()
  await page.getByRole('button', { name: '数据', exact: true }).click({ force: true })
  await expect(page.getByRole('heading', { name: '历史数据' })).toBeVisible()
  await expect(page.getByText('SQLite 总占用')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('heading', { name: '最近 15 分钟 K 线' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '最近聚合成交' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'LIVE', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toHaveCount(0)

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(hasHorizontalOverflow).toBe(false)
})

test('price chart renders clean ATR lines and semantic trade markers', async ({ page }) => {
  const pageErrors: string[] = []
  let equityHistoryRequests = 0
  let ohlcvHistoryRequests = 0
  page.on('pageerror', (error) => pageErrors.push(error.message))
  const now = Date.now()
  await page.route('**/api/accounts/soxl_perp/equity*', async (route) => {
    if (new URL(route.request().url()).searchParams.has('before_ms')) equityHistoryRequests += 1
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([
        { timestamp_ms: now - 50_000, price: '100', equity: '100000', cash: '100000', quantity: '0', unrealized_pnl: '0', atr: '2', trailing_stop: '102', relation: 'below' },
        { timestamp_ms: now - 40_000, price: '100', equity: '99900', cash: '0', quantity: '10', unrealized_pnl: '-100', atr: '2.1', trailing_stop: '98', relation: 'above' },
        { timestamp_ms: now - 30_000, price: '110', equity: '100097.9', cash: '100097.9', quantity: '0', unrealized_pnl: '0', atr: '2.3', trailing_stop: '107.7', relation: 'above' },
        { timestamp_ms: now - 20_000, price: '108', equity: '99996', cash: '0', quantity: '10', unrealized_pnl: '-101.9', atr: '2.2', trailing_stop: '105.8', relation: 'above' },
        { timestamp_ms: now - 10_000, price: '99', equity: '100005.83', cash: '100005.83', quantity: '0', unrealized_pnl: '0', atr: '2.4', trailing_stop: '101.4', relation: 'below' },
      ]),
    })
  })
  await page.route('**/api/fills?*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([
        { id: 'sell-loss', account_id: 'soxl_perp', side: 'SELL', timestamp_ms: now - 10_000, price: '99', quantity: '10', notional: '990', fee: '0.99', reason: 'test', source: 'test' },
        { id: 'buy-loss', account_id: 'soxl_perp', side: 'BUY', timestamp_ms: now - 20_000, price: '108', quantity: '10', notional: '1080', fee: '1.08', reason: 'test', source: 'test' },
        { id: 'sell-profit', account_id: 'soxl_perp', side: 'SELL', timestamp_ms: now - 30_000, price: '110', quantity: '10', notional: '1100', fee: '1.1', reason: 'test', source: 'test' },
        { id: 'buy-profit', account_id: 'soxl_perp', side: 'BUY', timestamp_ms: now - 40_000, price: '100', quantity: '10', notional: '1000', fee: '1', reason: 'test', source: 'test' },
        { id: 'buy-close-short', account_id: 'soxl_perp', side: 'BUY', timestamp_ms: now - 45_000, price: '100', quantity: '10', notional: '1000', fee: '1', reason: 'test', source: 'test', position_effect: 'CLOSE', position_before: '-10', position_after: '0', realized_pnl: '99' },
        { id: 'sell-open-short', account_id: 'soxl_perp', side: 'SELL', timestamp_ms: now - 50_000, price: '110', quantity: '10', notional: '1100', fee: '1', reason: 'test', source: 'test', position_effect: 'OPEN', position_before: '0', position_after: '-10', realized_pnl: '-1' },
      ]),
    })
  })
  await page.route('**/api/market/ohlcv?*', async (route) => {
    if (new URL(route.request().url()).searchParams.has('before_ms')) ohlcvHistoryRequests += 1
    const starts = [now - 60_000, now - 50_000, now - 40_000, now - 30_000, now - 20_000]
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(starts.map((start, index) => ({
        instrument_id: 'soxl_perp',
        symbol: 'SOXLUSDT',
        interval_minutes: 15,
        start_ms: start,
        end_ms: start + 10_000,
        open: String([99, 100, 103, 110, 108][index]),
        high: String([101, 104, 112, 111, 109][index]),
        low: String([98, 99, 102, 107, 97][index]),
        close: String([100, 103, 110, 108, 99][index]),
        volume: String(100 + index),
        trade_count: 10 + index,
        is_closed: true,
        source: 'binance_public_kline_rest',
        updated_at_ms: now,
      }))),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '回放', exact: true }).click()
  await page.getByRole('button', { name: 'SOXL/USDT PERP', exact: true }).click()
  await page.waitForTimeout(100)

  expect(pageErrors).toEqual([])
  await expect(page.getByRole('heading', { name: '价格与交易信号' })).toBeVisible()
  await expect(page.getByText('ATR 止损线').first()).toBeVisible()
  await expect(page.getByTestId('trade-marker-long')).toHaveCount(2)
  await expect(page.getByTestId('trade-marker-short')).toHaveCount(1)
  await expect(page.getByTestId('trade-marker-close')).toHaveCount(3)
  await expect(page.locator('.trade-range')).toHaveCount(0)
  const recentTrades = page.locator('.fills-panel')
  await expect(recentTrades.getByRole('heading', { name: '最近成交' })).toBeVisible()
  await expect(recentTrades.getByText('总手续费')).toBeVisible()
  await expect(recentTrades.getByText('净盈亏（含费）')).toBeVisible()
  await expect(recentTrades.getByTestId('completed-trade-row')).toHaveCount(3)
  await expect(recentTrades.getByText('LONG', { exact: true })).toHaveCount(2)
  await expect(recentTrades.getByText('SHORT', { exact: true })).toHaveCount(1)
  await expect(recentTrades.getByText('LONG -> CLOSE')).toHaveCount(2)
  await expect(recentTrades.getByText('SHORT -> CLOSE')).toHaveCount(1)
  await expect(page.locator('.recharts-line-curve')).toHaveCount(3)
  const atrLine = page.locator('.recharts-line-curve[stroke="#d39a18"]')
  await expect(atrLine).toBeVisible()
  await expect(atrLine).not.toHaveAttribute('stroke-dasharray')
  await page.getByTestId('trade-marker-short').hover({ force: true })
  await expect(page.locator('.price-tooltip')).toContainText('交易动作')
  await expect(page.locator('.price-tooltip')).toContainText('SHORT')
  await expect(page.locator('.price-tooltip')).toContainText('市场价格')
  await expect(page.locator('.price-tooltip')).toContainText('ATR 止损线')
  await expect(page.locator('.price-tooltip')).not.toContainText('NaN')
  await expect(page.locator('.price-tooltip')).toContainText('可视区间涨跌')
  await page.getByRole('button', { name: '放大价格图' }).click()
  await page.getByRole('button', { name: '向右滚动价格图' }).click()
  await page.getByRole('button', { name: '显示全部价格数据' }).click()
  await expect(page.locator('.recharts-brush')).toBeVisible()
  await page.getByRole('button', { name: '加载更早价格数据' }).click()
  await expect.poll(() => equityHistoryRequests).toBe(1)
  await expect(page.getByTestId('official-kline-chart')).toBeVisible()
  await expect(page.locator('.kline-candle')).toHaveCount(5)
  await expect(page.getByTestId('kline-marker-long')).toHaveCount(2)
  await expect(page.getByTestId('kline-marker-short')).toHaveCount(1)
  await expect(page.getByTestId('kline-marker-close')).toHaveCount(3)
  await page.getByRole('button', { name: '放大K线' }).click()
  await page.getByRole('button', { name: '向右滚动K线' }).click()
  await page.getByRole('button', { name: '显示全部K线' }).click()
  await page.getByRole('button', { name: '加载更早K线' }).click()
  await expect.poll(() => ohlcvHistoryRequests).toBe(1)
})
