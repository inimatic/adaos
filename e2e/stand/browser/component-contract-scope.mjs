// Independent query-scope behavior; no compiler equality as the browser oracle.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal, revealPrototypeWidget } from './prototype-navigation.mjs'

const scenario = process.env.ADAOS_E2E_SCENARIO_ID
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const output = process.env.ADAOS_E2E_OUTPUT
if (process.env.ENV_TYPE !== 'dev' || !scenario?.startsWith('test_semantic_') || !webspace?.endsWith('-dev')) throw new Error('Owned DEV fixture required')
const widgets = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_EXPECTED_WEBUI, 'utf8')).ui.application.desktop.pageSchema.widgets
const collection = widgets.find(widget => widget.id === 'records-list')
const browser = await chromium.launch({ headless: true })
const report = { ok: false, samples: [], modelCalls: 0 }
await fs.mkdir(output, { recursive: true })
try {
  for (const [layout, viewport] of [['wide', { width: 1440, height: 1000 }], ['compact', { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      for (const [key, value] of Object.entries({ adaos_device_id: `scope-e2e:${webspace}`, adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_hub_url: hub, adaos_local_hub_token: token, adaos_local_subnet_id: subnet,
        adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en' })) localStorage.setItem(key, value)
    }, { hub: process.env.ADAOS_E2E_HUB_URL, token: process.env.ADAOS_E2E_HUB_TOKEN, subnet: process.env.ADAOS_E2E_SUBNET_ID, webspace })
    const page = await context.newPage()
    const sample = { layout, mutations: 0, checks: [], errors: [] }
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(String(error)))
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/resources/operate') sample.mutations++ })
    await installSystemOverlayDismissal(page, () => {})
    try {
      const url = new URL(process.env.ADAOS_E2E_CLIENT_URL)
      for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: process.env.ADAOS_E2E_SUBNET_ID,
        webspace_id: webspace, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await page.waitForFunction(expected => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materializationReady
        && window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === expected, scenario, { timeout: 60_000 })
      await revealPrototypeWidget(page, widgets, collection)
      const table = page.locator('[data-webui-widget-id="records-list"]').filter({ visible: true }).first()
      await expect(table).toContainText('Second task', { timeout: 30_000 })
      await expect(table).not.toContainText('First task')
      sample.checks.push('new-permanent-scope')
      const toolbar = page.locator('[data-webui-widget-id="queries-records-list"]').filter({ visible: true }).first()
      await toolbar.locator('input[type=search]').fill('not found')
      await expect(table).not.toContainText('Second task')
      await toolbar.locator('.query-toolbar__reset').click()
      await expect(table).toContainText('Second task')
      await expect(table).not.toContainText('First task')
      sample.checks.push('reset-keeps-new-scope')
      if (sample.mutations || sample.errors.length) throw new Error('Scope navigation mutated records or produced renderer errors')
      sample.ok = true
    } catch (error) { sample.ok = false; sample.error = String(error) }
    await page.screenshot({ path: path.join(output, layout + '.png'), fullPage: true })
    await context.close()
  }
  report.ok = report.samples.every(sample => sample.ok)
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
}
console.log(JSON.stringify(report))
if (!report.ok) process.exitCode = 1
