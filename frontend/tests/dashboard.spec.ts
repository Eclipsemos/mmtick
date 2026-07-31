import { expect, test } from '@playwright/test'

test('paper console renders live state and operational views', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '实时模拟盘' })).toBeVisible()
  await expect(page.getByText('SOXL/USDT PERP', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('当前资金费')).toBeVisible()
  await page.getByRole('button', { name: 'SOXLB/USDT', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'SOXLB/USDT' })).toBeVisible()
  await page.getByRole('button', { name: 'SOXL/USDT PERP', exact: true }).click()
  await expect(page.getByText('账户净值')).toBeVisible()
  await expect(page.getByText('夏普率')).toBeVisible()
  await expect(page.getByText('交易胜率')).toBeVisible()
  await expect(page.getByText('交易决策状态')).toBeVisible()
  await expect(page.getByText('下一触发条件')).toBeVisible()
  await expect(page.getByText('ATR 实时状态')).toBeVisible()
  await expect(page.getByText('信号防抖')).toBeVisible()
  await expect(page.getByText('2.0s')).toBeVisible()
  await expect(page.getByRole('button', { name: '放大价格图' })).toBeVisible()
  await expect(page.getByRole('button', { name: '缩小价格图' })).toBeVisible()
  await expect(page.locator('.recharts-brush')).toBeVisible()

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
})
