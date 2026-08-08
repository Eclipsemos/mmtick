import { expect, test } from '@playwright/test'

test('paper console renders operational views and switches to the protected live account', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '模拟交易' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'PAPER' })).toHaveClass(/active/)
  await expect(page.getByRole('region', { name: 'SOXL 合约实盘就绪状态' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'SOXL/USDT PERP LONG ONLY' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('LONG ONLY', { exact: true })).toBeVisible()
  const paperAccounts = page.locator('.account-switch button')
  await expect(paperAccounts).toHaveCount(2)
  await expect(paperAccounts.nth(0)).toHaveText('SOXL/USDT PERP LONG ONLY')
  await expect(paperAccounts.nth(1)).toHaveText('SOXL/USDT PERP')
  await expect(page.getByText('2x isolated')).toHaveCount(0)
  await expect(page.getByText('当前资金费')).toBeVisible()
  await page.getByRole('button', { name: 'SOXL/USDT PERP', exact: true }).click()
  await expect(page.getByText('LONG / SHORT')).toBeVisible()
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

  await page.getByRole('button', { name: /订单/ }).click()
  await expect(page.getByRole('heading', { name: '全部订单' })).toBeVisible()
  await page.getByRole('button', { name: /收益明细/ }).click()
  await expect(page.getByRole('heading', { name: '收益明细' })).toBeVisible()
  await expect(page.getByText('近 30 日收益')).toBeVisible()
  await expect(page.getByRole('heading', { name: '最近 30 天收益日历' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '每周收益' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '每月收益' })).toBeVisible()
  await expect(page.getByText('年化收益')).toBeVisible()
  await page.getByRole('button', { name: /仓库/ }).click({ force: true })
  await expect(page.getByRole('heading', { name: '数据仓库' })).toBeVisible()
  await expect(page.getByText('SQLite 总占用')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('heading', { name: '最近 15 分钟 K 线' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '最近聚合成交' })).toBeVisible()
  await page.getByRole('button', { name: /监控/ }).click()

  await expect(page.getByRole('button', { name: '暂停' })).toBeVisible()

  await page.getByRole('button', { name: 'LIVE', exact: true }).click()
  await expect(page.getByRole('heading', { name: '实盘交易' })).toBeVisible()
  const liveReadiness = page.getByRole('region', { name: 'SOXL 合约实盘就绪状态' })
  await expect(liveReadiness.locator('.live-readiness-state strong')).toHaveText(/ARMED|OBSERVE_ONLY|BLOCKED|DISABLED/)
  await expect(liveReadiness.getByText('公开接口')).toBeVisible()
  await expect(liveReadiness.getByText('订单开关')).toBeVisible()
  await expect(page.getByRole('button', { name: 'LIVE', exact: true })).toHaveClass(/active/)
  await expect(page.getByRole('button', { name: 'SOXL/USDT PERP LIVE', exact: true })).toHaveCount(1, { timeout: 15_000 })
  await expect(page.locator('.account-switch button')).toHaveCount(1)
  await expect(page.getByRole('button', { name: '平仓', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '停止策略', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '锁定' })).toBeVisible()
  await expect(page.getByText('Binance 实际成交')).toBeVisible()
  await expect(page.getByText('LONG ONLY', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '显示策略参数' }).click()
  const strategyParameters = page.getByRole('group', { name: '当前策略参数' })
  await expect(strategyParameters.getByText('ATR 周期')).toBeVisible()
  await expect(strategyParameters.getByText('32', { exact: true })).toBeVisible()
  await expect(strategyParameters.getByText('ATR 倍数')).toBeVisible()
  await expect(strategyParameters.getByText('3', { exact: true })).toBeVisible()
  await expect(strategyParameters.getByText('效率比周期')).toBeVisible()
  await expect(strategyParameters.getByText('最低趋势效率')).toBeVisible()
  await expect(strategyParameters.getByText('反向确认距离')).toBeVisible()
  await expect(strategyParameters.getByText('启动趋势对齐')).toBeVisible()
  await page.getByRole('button', { name: '隐藏策略参数' }).click()
  await page.getByRole('button', { name: 'PAPER', exact: true }).click()
  await expect(page.getByRole('heading', { name: '模拟交易' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'SOXL/USDT PERP', exact: true })).toBeVisible()

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(hasHorizontalOverflow).toBe(false)
  expect(consoleErrors).toEqual([])
})

test('live market close requires a second explicit confirmation', async ({ page }) => {
  let flattenCalls = 0
  await page.route('**/api/live/overview', async (route) => {
    const response = await route.fetch()
    const body = await response.json()
    body.accounts[0].quantity = '1'
    await route.fulfill({ response, json: body })
  })
  await page.route('**/api/live/flatten', async (route) => {
    flattenCalls += 1
    expect(route.request().postDataJSON()).toEqual({ confirm: 'FLATTEN_SOXLUSDT' })
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        already_flat: false,
        flat_confirmed: true,
        orders: [{
          client_order_id: 'mmt-close-l-test',
          side: 'SELL',
          position_side: 'LONG',
          quantity: '1',
          status: 'FILLED',
        }],
      }),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: 'LIVE', exact: true }).click()
  const flatten = page.getByRole('button', { name: '平仓', exact: true })
  await expect(flatten).toBeEnabled({ timeout: 10_000 })
  await flatten.click()
  const dialog = page.getByRole('dialog', { name: '确认平仓' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('不会自动停止策略')).toBeVisible()
  await dialog.getByRole('button', { name: '取消' }).click()
  expect(flattenCalls).toBe(0)

  await flatten.click()
  await page.getByRole('dialog', { name: '确认平仓' }).getByRole('button', { name: '确认平仓' }).click()
  await expect.poll(() => flattenCalls).toBe(1)
  await expect(page.getByRole('status').filter({ hasText: '平仓已成交' })).toBeVisible()
  await page.unrouteAll({ behavior: 'ignoreErrors' })
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

test('remote live access requests the independent operator token', async ({ page }) => {
  await page.route('**/api/live/session', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      authenticated: false,
      configured: true,
      local_unlock_available: false,
    }),
  }))
  await page.route('**/api/live/unlock', (route) => route.fulfill({
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Invalid LIVE operator token' }),
  }))

  await page.goto('/')
  await page.getByRole('button', { name: 'LIVE', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '验证实盘访问' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('操作员令牌').fill('not-the-operator-token')
  await dialog.getByRole('button', { name: '进入 LIVE' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('操作员令牌不正确。')
  await expect(page.getByRole('button', { name: 'PAPER', exact: true })).toHaveClass(/active/)
})
