import { expect, test } from '@playwright/test'

test('paper console renders live state and operational views', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '实时模拟盘' })).toBeVisible()
  await expect(page.getByText('SOXLB/USDT', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('账户净值')).toBeVisible()
  await expect(page.getByText('ATR 实时状态')).toBeVisible()

  await page.getByRole('button', { name: /订单/ }).click()
  await expect(page.getByRole('heading', { name: '全部订单' })).toBeVisible()
  await page.getByRole('button', { name: /事件/ }).click()
  await expect(page.getByRole('heading', { name: '事件日志' })).toBeVisible()
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
        { timestamp_ms: now - 30_000, price: '100', equity: '100000', cash: '100000', quantity: '0', unrealized_pnl: '0', atr: '2', trailing_stop: '102', relation: 'below' },
        { timestamp_ms: now - 20_000, price: '101', equity: '99900', cash: '0', quantity: '989', unrealized_pnl: '-100', atr: '2.1', trailing_stop: '98.9', relation: 'above' },
        { timestamp_ms: now - 10_000, price: '110', equity: '108700', cash: '108700', quantity: '0', unrealized_pnl: '0', atr: '2.3', trailing_stop: '107.7', relation: 'above' },
      ]),
    })
  })
  await page.route('**/api/fills?*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([
        { id: 'sell', account_id: 'soxlb', side: 'SELL', timestamp_ms: now - 10_000, price: '110', quantity: '10', notional: '1100', fee: '1.1', reason: 'test', source: 'test' },
        { id: 'buy', account_id: 'soxlb', side: 'BUY', timestamp_ms: now - 20_000, price: '100', quantity: '10', notional: '1000', fee: '1', reason: 'test', source: 'test' },
      ]),
    })
  })

  await page.goto('/')
  await page.waitForTimeout(100)

  expect(pageErrors).toEqual([])
  await expect(page.getByRole('heading', { name: '价格与交易信号' })).toBeVisible()
  await expect(page.getByText('ATR 止损线').first()).toBeVisible()
  await expect(page.getByTestId('trade-marker-buy')).toBeVisible()
  await expect(page.getByTestId('trade-marker-sell')).toBeVisible()
  await expect(page.getByText('+9.78%')).toBeVisible()
  await expect(page.locator('.recharts-line-curve')).toHaveCount(3)
})
