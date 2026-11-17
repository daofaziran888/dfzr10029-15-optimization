import { expect, test } from '@playwright/test'

test('查询缓存：同版本同参数只执行一次，切换筛选显示命中标记，分页保持筛选条件', async ({ page }) => {
  test.setTimeout(120_000)
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) errors.push(`${r.status()} ${r.url()}`) })

  await page.goto('/query_builder')
  await page.waitForTimeout(2000)

  // 选择模板并填写日期、门店筛选
  await page.getByTestId('execute-template').selectOption({ index: 0 })
  await page.getByTestId('execute-date').fill('2026-11-15')
  await page.getByTestId('execute-store').fill('S001')

  // 首次执行：重新执行
  await page.getByTestId('execute-submit').click()
  await expect(page.getByTestId('result-marker')).toHaveText('重新执行')
  const firstSnapshot = await page.locator('.result-meta', { hasText: '快照' }).innerText()

  // 相同模板版本和参数范围再次执行：命中，同一份快照，只执行一次
  await page.getByTestId('execute-submit').click()
  await expect(page.getByTestId('result-marker')).toHaveText('命中')
  await expect(page.locator('.result-meta', { hasText: '快照' })).toHaveText(firstSnapshot)

  // 切换日期筛选：重新执行
  await page.getByTestId('execute-date').fill('2026-11-16')
  await page.getByTestId('execute-submit').click()
  await expect(page.getByTestId('result-marker')).toHaveText('重新执行')

  // 命中记录表格已写入记录，命中/重新执行徽章都存在
  await expect(page.locator('[data-testid^="hit-row-"]').first()).toBeVisible()
  const badges = await page.locator('[data-testid^="hit-row-"] .badge-hit, [data-testid^="hit-row-"] .badge-miss').allInnerTexts()
  expect(badges).toContain('命中')
  expect(badges).toContain('重新执行')

  // 按业务字段筛选命中记录，分页保持筛选条件
  await page.getByTestId('hit-filter-命中状态').fill('命中')
  await page.getByTestId('hit-query').click()
  await page.waitForTimeout(800)
  const filteredRows = page.locator('[data-testid^="hit-row-"]')
  await expect(filteredRows.first()).toBeVisible()
  for (const text of await filteredRows.allInnerTexts()) {
    expect(text).toContain('命中')
  }

  // 核对命中记录，得到唯一核对结果
  const verifyBtn = page.locator('[data-testid^="hit-verify-"]').first()
  await verifyBtn.click()
  await page.waitForTimeout(800)
  await expect(page.locator('[data-testid^="hit-row-"] .badge-ok').first()).toBeVisible()

  // 重置筛选后记录仍可回查
  await page.getByTestId('hit-reset').click()
  await page.waitForTimeout(800)
  await expect(page.locator('[data-testid^="hit-row-"]').first()).toBeVisible()

  expect(errors).toEqual([])
})

test('发布新版本后旧参数范围按模板版本失效，重新执行全量扫描', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/query_builder')
  await page.waitForTimeout(2000)

  await page.getByTestId('execute-template').selectOption({ index: 0 })
  await page.getByTestId('execute-date').fill('2026-11-17')
  await page.getByTestId('execute-store').fill('S009')
  await page.getByTestId('execute-submit').click()
  await expect(page.getByTestId('result-marker')).toHaveText('重新执行')
  await page.getByTestId('execute-submit').click()
  await expect(page.getByTestId('result-marker')).toHaveText('命中')

  // 首行种子模板为草稿：首次发布保持 v1，再次发布才生成 v2 新版本
  const firstRow = page.locator('tbody tr[data-testid^="row-"]').first()
  await firstRow.getByRole('button', { name: '发布模板', exact: true }).click()
  await page.waitForTimeout(800)
  await firstRow.getByRole('button', { name: '发布模板', exact: true }).click()
  await page.waitForTimeout(1000)

  // 旧参数范围仍在缓存中，但模板版本变化 -> 重新执行
  await page.getByTestId('execute-submit').click()
  await expect(page.getByTestId('result-marker')).toHaveText('重新执行')
  // 再执行一次同口径 -> 命中新版本的结果
  await page.getByTestId('execute-submit').click()
  await expect(page.getByTestId('result-marker')).toHaveText('命中')
})

test('缓存管理页跨模块只读回查查询缓存命中记录', async ({ page }) => {
  test.setTimeout(120_000)
  // 先在 OLAP 查询页产生一条命中记录
  await page.goto('/query_builder')
  await page.waitForTimeout(2000)
  await page.getByTestId('execute-template').selectOption({ index: 0 })
  await page.getByTestId('execute-date').fill('2026-11-18')
  await page.getByTestId('execute-store').fill('S010')
  await page.getByTestId('execute-submit').click()
  await expect(page.getByTestId('result-marker')).toBeVisible()

  await page.goto('/cache')
  await page.waitForTimeout(2000)
  await expect(page.locator('[data-testid="query-hit-panel"]')).toBeVisible()
  await expect(page.locator('[data-testid^="query-hit-row-"]').first()).toBeVisible()

  // 跨模块筛选保持口径
  await page.getByTestId('query-hit-filter-模板编号').fill('QUER')
  await page.getByTestId('query-hit-search').click()
  await page.waitForTimeout(800)
  const rows = page.locator('[data-testid^="query-hit-row-"]')
  await expect(rows.first()).toBeVisible()
  for (const text of await rows.allInnerTexts()) {
    expect(text).toContain('QUER')
  }
})
