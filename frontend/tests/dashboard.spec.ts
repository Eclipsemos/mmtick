import { expect, test } from '@playwright/test'

test('paper console renders live state and operational views', async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '实时模拟盘' })).toBeVisible()
  await expect(page.getByText('SOXLB/USDT', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('账户净值')).toBeVisible()
  await expect(page.getByText('ATR 移动线')).toBeVisible()

  await page.getByRole('button', { name: /订单/ }).click()
  await expect(page.getByRole('heading', { name: '全部订单' })).toBeVisible()
  await page.getByRole('button', { name: /事件/ }).click()
  await expect(page.getByRole('heading', { name: '事件日志' })).toBeVisible()
  await page.getByRole('button', { name: /监控/ }).click()

  if (testInfo.project.name === 'desktop') {
    await page.getByRole('button', { name: '暂停' }).click()
    await expect(page.getByRole('button', { name: '恢复' })).toBeVisible()
    await page.getByRole('button', { name: '恢复' }).click()
    await expect(page.getByRole('button', { name: '暂停' })).toBeVisible()
  }

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(hasHorizontalOverflow).toBe(false)
  expect(consoleErrors).toEqual([])
})

