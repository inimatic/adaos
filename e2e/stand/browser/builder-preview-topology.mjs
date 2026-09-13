import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const host = process.env.ADAOS_E2E_WEBSPACE_ID
const scenario = process.env.ADAOS_E2E_SCENARIO_ID
const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8778'
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT)
if (process.env.ENV_TYPE !== 'dev' || !host || !scenario || !token || !subnet) throw new Error('Explicit DEV Builder scope required')
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const report = { host, scenario, passed: false, errors: [] }
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ hub, token, subnet, host }) => {
    window.__ADAOS_DEBUG__ = true
    window.__ADAOS_BASE__ = hub
    window.__ADAOS_TOKEN__ = token
    for (const [key, value] of Object.entries({
      adaos_device_id: 'e2e-preview-topology', adaos_webspace_id: host,
      adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1',
      adaos_hub_token: token, adaos_local_subnet_id: subnet,
      adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo',
    })) localStorage.setItem(key, value)
  }, { hub, token, subnet, host })
  const page = await context.newPage()
  page.on('pageerror', error => report.errors.push(error.message))
  page.setDefaultTimeout(30_000)
  const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
  for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo',
    subnet_id: subnet, webspace_id: host, space_kind: 'workspace',
    expected_scenario_id: 'builder', try_local_hub: '1' })) url.searchParams.set(key, value)
  try {
    await page.goto(url.href, { waitUntil: 'domcontentloaded' })
    await page.waitForFunction(() => {
      const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
      return sync?.materializationReady && sync.materialization.currentScenario === 'builder'
    }, undefined, { timeout: 60_000 })
    if (process.env.ADAOS_E2E_PROJECT_SEARCH) {
      await page.getByRole('button', { name: /choose project|выбрать проект/i }).click()
      const picker = page.locator('ion-modal').filter({ has: page.locator('ada-table-widget') }).last()
      await picker.locator('ada-selector-widget .selector__control').click()
      await page.locator('button.selector__option').filter({ hasText: /^(Test applications|Тестовые приложения)$/ }).click()
      const search = picker.locator('ada-table-widget input[type=search]')
      await search.fill(process.env.ADAOS_E2E_PROJECT_SEARCH)
      const row = picker.locator('ada-table-widget tbody tr.row-selectable').filter({ hasText: process.env.ADAOS_E2E_PROJECT_SEARCH }).first()
      await row.click()
      await picker.waitFor({ state: 'hidden' })
    }
    await page.getByText(/^(overview|обзор)$/i).first().click()
    const open = page.getByRole('button', { name: /open preview in a new window|открыть просмотр в новом окне/i })
    await open.waitFor({ state: 'visible' })
    const [popup] = await Promise.all([context.waitForEvent('page'), open.click()])
    await popup.waitForLoadState('domcontentloaded')
    await popup.waitForFunction(expected => {
      const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
      return sync?.materializationReady && sync.materialization.currentScenario === expected
    }, scenario, { timeout: 60_000 })
    const previewUrl = new URL(popup.url())
    report.navigationUrl = previewUrl.href
    expect(previewUrl.searchParams.get('webspace_id')).toBe(`${host}-dev`)
    if (process.env.ADAOS_E2E_EXPECTED_REVISION) {
      expect(previewUrl.searchParams.get('expected_revision')).toBe(process.env.ADAOS_E2E_EXPECTED_REVISION)
    }
    if (process.env.ADAOS_E2E_PREVIEW_TEXT) {
      await expect(popup.locator('body')).toContainText(process.env.ADAOS_E2E_PREVIEW_TEXT, { timeout: 30_000 })
    }
    await popup.evaluate(() => document.fonts.ready)
    await expect(popup.getByText(/^Loading data\.\.\.$/)).toHaveCount(0, { timeout: 30_000 })
    report.previewUrl = popup.url()
    report.previewText = await popup.locator('body').innerText()
    await popup.screenshot({ path: path.join(output, 'preview-wide.png'), fullPage: true })
    await popup.setViewportSize({ width: 390, height: 844 })
    await popup.screenshot({ path: path.join(output, 'preview-compact.png'), fullPage: true })
    report.passed = true
  } catch (error) {
    report.failure = error.message
    report.builderText = await page.locator('body').innerText()
    await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true })
  }
} finally { await browser.close() }
await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify({ passed: report.passed, failure: report.failure, previewUrl: report.previewUrl }))
if (!report.passed) process.exitCode = 1
