import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'

const { ADAOS_E2E_PROJECT_SEARCH: search, ADAOS_E2E_SCENARIO_ID: scenario,
  ADAOS_E2E_WEBSPACE_ID: webspace, ADAOS_E2E_SUBNET_ID: subnet,
  ADAOS_E2E_HUB_TOKEN: token } = process.env
if (process.env.ENV_TYPE !== 'dev' || !search || !scenario || !webspace || !subnet || !token) {
  throw new Error('An explicit dev node, Builder webspace, test search and target scenario are required')
}
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'artifacts/builder-project-review')
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo',
  subnet_id: subnet, webspace_id: webspace, space_kind: 'development',
  expected_scenario_id: 'builder', try_local_hub: '1' })) url.searchParams.set(key, value)
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const report = { scenario, webspace, search, passed: false, errors: [], responses: [] }
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ hub, token, subnet, webspace }) => {
    window.__ADAOS_DEBUG__ = true
    window.__ADAOS_BASE__ = hub
    window.__ADAOS_TOKEN__ = token
    for (const [key, value] of Object.entries({
      adaos_device_id: 'e2e-builder-project-review', adaos_webspace_id: webspace,
      adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1',
      adaos_hub_token: token, adaos_local_subnet_id: subnet,
      adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo',
    })) localStorage.setItem(key, value)
  }, { hub, token, subnet, webspace })
  const page = await context.newPage()
  page.setDefaultTimeout(30_000)
  page.setDefaultNavigationTimeout(60_000)
  page.on('pageerror', error => report.errors.push(error.message))
  page.on('response', response => {
    if (response.status() >= 400) report.responses.push({ url: response.url(), status: response.status() })
  })
  try {
    await page.goto(url.href, { waitUntil: 'domcontentloaded' })
    await page.waitForFunction(() => {
      const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
      return sync?.materializationReady && sync.materialization.currentScenario === 'builder'
    }, undefined, { timeout: 60_000 })
    await page.getByRole('button', { name: /choose project|выбрать проект/i }).click()
    const picker = page.locator('ion-modal').filter({ has: page.locator('ada-table-widget') }).last()
    const rows = picker.locator('ada-table-widget tbody tr.row-selectable')
    await rows.first().waitFor({ state: 'visible', timeout: 30_000 })
    const updated = picker.locator('th').filter({ hasText: /updated|обновлено/i })
    if (await updated.getAttribute('aria-sort') !== 'descending') throw new Error('Expected newest development changes first')
    await picker.getByRole('button', { name: /^(application|приложение)$/i }).click()
    const applicationHeader = picker.locator('th').filter({ hasText: /application|приложение/i }).first()
    if (await applicationHeader.getAttribute('aria-sort') !== 'ascending') throw new Error('Application sort was not applied')
    await updated.getByRole('button').click()
    await updated.getByRole('button').click()
    report.sorting = true
    const firstPage = await rows.allTextContents()
    if (firstPage.length !== 10) throw new Error('Expected an initial 10-row page')
    await picker.getByRole('button', { name: /next page|следующая страница/i }).click()
    await page.waitForFunction(first => {
      const row = document.querySelector('ion-modal ada-table-widget tbody tr.row-selectable')
      return row && row.textContent !== first
    }, firstPage[0])
    report.pagination = { firstPage, secondPage: await rows.allTextContents() }
    await picker.getByRole('button', { name: /previous page|предыдущая страница/i }).click()
    await picker.locator('ada-selector-widget .selector__control').click()
    await page.locator('button.selector__option').filter({ hasText: /^(Test applications|Тестовые приложения)$/ }).click()
    await page.waitForFunction(() => {
      const rows = [...document.querySelectorAll('ion-modal ada-table-widget tbody tr.row-selectable')]
      return rows.length > 0 && rows.every(row => row.textContent.includes('[TEST]'))
    })
    report.testFilter = true
    const reloadResponse = page.waitForResponse(response => {
      try { return response.request().postDataJSON()?.tool === 'builder_sdk_control_skill:list_projects' && response.ok() }
      catch { return false }
    })
    await picker.getByRole('button', { name: /reload table|обновить таблицу/i }).click()
    const freshCatalog = await (await reloadResponse).json()
    report.reloadedCatalog = { ok: freshCatalog.ok, renamedTitles: (freshCatalog.result || []).filter(item => item.title?.startsWith('+')).map(item => item.title) }
    await picker.locator('.table-pagination__size select').selectOption('50')
    await picker.locator('ada-toggle-widget ion-toggle').click()
    await page.waitForFunction(() => Object.keys(localStorage).some(key => {
      if (!key.includes('table-preferences:v1:')) return false
      const value = JSON.parse(localStorage.getItem(key))
      return value.pageSize === 50 && value.filters?.projectPickerSample === 'test' && value.filters?.projectPickerArchived === true
    }))
    const preferenceSnapshot = () => page.evaluate(() => ({
      stored: Object.fromEntries(Object.keys(localStorage).filter(key => key.includes('table-preferences:v1:')).map(key => [key, JSON.parse(localStorage.getItem(key))])),
      tables: [...document.querySelectorAll('ada-table-widget')].map(element => {
        const component = window.ng?.getComponent(element)
        return component ? { id: component.widget?.id, pageSize: component.pageSize, key: component.preferenceKey, scope: component.state?.getActiveScope?.() } : null
      }),
    }))
    report.preferencesBeforeReload = await preferenceSnapshot()
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: /choose project|выбрать проект/i }).click()
    await rows.first().waitFor({ state: 'visible', timeout: 30_000 })
    report.preferencesAfterReload = await preferenceSnapshot()
    if (await picker.locator('.table-pagination__size select').inputValue() !== '50') throw new Error('Page size was not restored')
    if (!/test applications|тестовые приложения/i.test(await picker.locator('ada-selector-widget .selector__control').innerText())) throw new Error('Filter control disagrees with restored table state')
    if (!await picker.locator('ada-toggle-widget ion-toggle').evaluate(element => element.checked)) throw new Error('Archive filter was not restored')
    report.persistedPreferences = true
    console.log('Table reload and persisted preferences passed')
    const searchInput = picker.locator('ada-table-widget input[type=search]')
    await searchInput.fill('e2e-no-such-project-20260911')
    await picker.getByText(/no matching projects|нет подходящих проектов/i).waitFor({ state: 'visible' })
    report.emptySearch = true
    await searchInput.fill(search)
    await searchInput.press('Enter')
    const row = picker.locator('ada-table-widget tbody tr').filter({ hasText: search }).first()
    await row.waitFor({ state: 'visible', timeout: 30_000 })
    report.selectedRow = await row.innerText()
    if (!report.selectedRow.includes('[TEST]')) throw new Error('Project is not marked TEST')
    await page.screenshot({ path: path.join(output, 'project-picker.png'), fullPage: true, animations: 'disabled' })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.screenshot({ path: path.join(output, 'project-picker-compact.png'), fullPage: true, animations: 'disabled' })
    await page.setViewportSize({ width: 1440, height: 1000 })
    console.log('Opening the selected test application')
    await row.click()
    await picker.waitFor({ state: 'hidden', timeout: 15_000 })
    const openPreview = page.getByRole('button', { name: /open preview in a new window|открыть просмотр в новом окне/i })
    await openPreview.waitFor({ state: 'visible', timeout: 30_000 })
    const [popup] = await Promise.all([
      context.waitForEvent('page', { timeout: 30_000 }), openPreview.click(),
    ])
    await popup.waitForLoadState('domcontentloaded')
    report.previewUrl = popup.url()
    await popup.waitForFunction(expected => {
      const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
      return sync?.materializationReady && sync.materialization.currentScenario === expected
    }, scenario, { timeout: 60_000 })
    report.previewText = await popup.locator('body').innerText()
    await popup.screenshot({ path: path.join(output, 'opened-preview.png'), fullPage: true })
    report.passed = true
  } catch (error) {
    report.failure = error.message
    report.builderText = await page.locator('body').innerText()
    await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true })
  }
} finally { await browser.close() }
await fs.writeFile(path.join(output, 'review.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify({ passed: report.passed, failure: report.failure, previewUrl: report.previewUrl }))
if (!report.passed) process.exitCode = 1
