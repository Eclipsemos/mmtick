import { expect, test } from '@playwright/test'

test('paper console renders live state and operational views', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '实时模拟盘' })).toBeVisible()
  await expect(page.getByText('SOXL/USDT PERP', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('LONG / SHORT')).toBeVisible()
  await expect(page.getByText('2x isolated')).toBeVisible()
  await expect(page.getByText('当前资金费')).toBeVisible()
  await page.getByRole('button', { name: 'SOXLB/USDT', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'SOXLB/USDT' })).toBeVisible()
  await expect(page.getByText('LONG ONLY')).toBeVisible()
  await page.getByRole('button', { name: 'SOXL/USDT PERP', exact: true }).click()
  await expect(page.getByText('账户净值')).toBeVisible()
  await expect(page.getByText('夏普率')).toBeVisible()
  await expect(page.getByText('交易胜率')).toBeVisible()
  await expect(page.getByText('交易决策状态')).toBeVisible()
  await expect(page.getByText('下一触发条件')).toBeVisible()
  await expect(page.getByText('ATR 实时状态')).toBeVisible()
  await expect(page.getByText('信号检测')).toBeVisible()
  await expect(page.getByText('每个成交 Tick')).toBeVisible()
  await expect(page.getByText('成交时点')).toBeVisible()
  await expect(page.getByText('下一成交 Tick')).toBeVisible()
  await expect(page.getByText('BUY 锁', { exact: true })).toBeVisible()
  await expect(page.getByText('SELL 锁', { exact: true })).toBeVisible()
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
  await page.getByRole('button', { name: /事件/ }).click()
  await expect(page.getByRole('heading', { name: '事件日志' })).toBeVisible()
  await page.getByRole('button', { name: /仓库/ }).click()
  await expect(page.getByRole('heading', { name: '数据仓库' })).toBeVisible()
  await expect(page.getByText('SQLite 总占用')).toBeVisible()
  await expect(page.getByRole('heading', { name: '最近 15 分钟 K 线' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '最近聚合成交' })).toBeVisible()
  await page.getByRole('button', { name: /监控/ }).click()

  await expect(page.getByRole('button', { name: '暂停' })).toBeVisible()

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(hasHorizontalOverflow).toBe(false)
  expect(consoleErrors).toEqual([])
})

test('price chart renders ATR line and trade percentage markers', async ({ page }) => {
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  const now = Date.now()
  await page.route('**/api/accounts/soxlb/equity*', async (route) => {
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
        { id: 'sell-loss', account_id: 'soxlb', side: 'SELL', timestamp_ms: now - 10_000, price: '99', quantity: '10', notional: '990', fee: '0.99', reason: 'test', source: 'test' },
        { id: 'buy-loss', account_id: 'soxlb', side: 'BUY', timestamp_ms: now - 20_000, price: '108', quantity: '10', notional: '1080', fee: '1.08', reason: 'test', source: 'test' },
        { id: 'sell-profit', account_id: 'soxlb', side: 'SELL', timestamp_ms: now - 30_000, price: '110', quantity: '10', notional: '1100', fee: '1.1', reason: 'test', source: 'test' },
        { id: 'buy-profit', account_id: 'soxlb', side: 'BUY', timestamp_ms: now - 40_000, price: '100', quantity: '10', notional: '1000', fee: '1', reason: 'test', source: 'test' },
      ]),
    })
  })
  await page.route('**/api/market/ohlcv?*', async (route) => {
    const starts = [now - 60_000, now - 50_000, now - 40_000, now - 30_000, now - 20_000]
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(starts.map((start, index) => ({
        instrument_id: 'soxlb',
        symbol: 'SOXLBUSDT',
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
  await page.getByRole('button', { name: 'SOXLB/USDT', exact: true }).click()
  await page.waitForTimeout(100)

  expect(pageErrors).toEqual([])
  await expect(page.getByRole('heading', { name: '价格与交易信号' })).toBeVisible()
  await expect(page.getByText('ATR 止损线').first()).toBeVisible()
  await expect(page.getByTestId('trade-marker-buy')).toHaveCount(2)
  await expect(page.getByTestId('trade-marker-sell')).toHaveCount(2)
  await expect(page.locator('.trade-range.profit')).toBeVisible()
  await expect(page.locator('.trade-range.loss')).toBeVisible()
  await expect(page.getByText('$97.90 · +9.78%')).toBeVisible()
  await expect(page.getByText('-$92.07 · -8.52%')).toBeVisible()
  await expect(page.locator('.recharts-line-curve')).toHaveCount(3)
  const atrLine = page.locator('.recharts-line-curve[stroke="#e8bd58"]')
  await expect(atrLine).toBeVisible()
  await expect(atrLine).not.toHaveAttribute('stroke-dasharray')
  await page.getByTestId('trade-marker-buy').first().hover({ force: true })
  await expect(page.locator('.price-tooltip')).toContainText('成交信号')
  await expect(page.locator('.price-tooltip')).toContainText('BUY')
  await expect(page.locator('.price-tooltip')).not.toContainText('NaN')
  await expect(page.locator('.price-tooltip')).toContainText('可视区间涨跌')
  await page.getByRole('button', { name: '放大价格图' }).click()
  await page.getByRole('button', { name: '向右滚动价格图' }).click()
  await page.getByRole('button', { name: '显示全部价格数据' }).click()
  await expect(page.locator('.recharts-brush')).toBeVisible()
  await expect(page.getByTestId('official-kline-chart')).toBeVisible()
  await expect(page.locator('.kline-candle')).toHaveCount(5)
  await expect(page.getByTestId('kline-marker-buy')).toHaveCount(2)
  await expect(page.getByTestId('kline-marker-sell')).toHaveCount(2)
  await page.getByRole('button', { name: '放大K线' }).click()
  await page.getByRole('button', { name: '向右滚动K线' }).click()
  await page.getByRole('button', { name: '显示全部K线' }).click()
})
