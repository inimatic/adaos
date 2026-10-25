// No model calls or application data edits. Use an explicitly isolated host.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const host = process.env.ADAOS_E2E_HOST
const output = process.env.ADAOS_E2E_OUTPUT
const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
if (!host?.startsWith('desktop-codex-builder-beta-') || !output || !token || !subnet) {
  throw new Error('Explicit isolated host, output and local owner session required')
}
const browser = await chromium.launch({ headless: true })
const report = { ok: false, providerCalls: 0, applicationDataWrites: 0, host, cases: [], errors: [] }
let inspectedPage
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  await context.addInitScript(({ token, subnet, hub }) => {
    if (location.href === 'about:blank') return
    for (const [key, value] of Object.entries({ adaos_local_hub_url: hub, adaos_local_hub_token: token,
      adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en' })) {
      localStorage.setItem(key, value)
    }
  }, { token, subnet, hub })
  const page = await context.newPage()
  inspectedPage = page
  page.on('pageerror', error => report.errors.push(String(error.message).slice(0, 500)))
  await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
    await page.locator('.component-updates-panel__tools button').last().dispatchEvent('click')
  })
  const open = async scenario => {
    await page.goto('about:blank')
    const response = await context.request.post(`${hub}/api/node/yjs/webspaces/${host}/scenario`, {
      headers: { 'X-AdaOS-Token': token }, data: { scenario_id: scenario, wait_for_rebuild: false,
        request_source: 'e2e.builder.beta.reconciliation' }, timeout: 120_000,
    })
    if (!response.ok()) throw new Error(`Isolated scenario switch failed: ${response.status()}`)
    const accepted = await response.json()
    if (accepted.ok !== true) throw new Error(`Scenario switch rejected: ${accepted.error || 'unknown'}`)
    await expect.poll(async () => {
      const state = await context.request.get(`${hub}/api/node/yjs/webspaces/${host}/materialization?include_runtime=0`, {
        headers: { 'X-AdaOS-Token': token }, timeout: 30_000,
      })
      const value = await state.json()
      if (value.rebuild?.status === 'failed') throw new Error(`Scenario materialization failed: ${value.rebuild.error}`)
      // After a Core restart an unchanged, ready snapshot has no in-process
      // rebuild job. Prove its scenario/readiness rather than requiring a job
      // that the idempotent switch correctly did not schedule.
      if (!value.rebuild?.pending && ['idle', 'ready'].includes(value.rebuild?.status)
        && value.materialization?.ready && !value.materialization?.stale
        && value.materialization?.current_scenario === scenario) return `${scenario}:ready`
      return `${value.rebuild?.scenario_id}:${value.rebuild?.status}`
    }, { timeout: 150_000, intervals: [500, 1000, 2000] }).toBe(`${scenario}:ready`)
    console.log(JSON.stringify({ phase: 'materialized', scenario }))
    const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100')
    for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
      webspace_id: host, expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
    await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  }
  await open('builder')
  const revisions = page.locator('[data-webui-widget-id="builder-runtime-revisions"]').filter({ visible: true }).last()
  await expect(revisions).toBeVisible({ timeout: 90_000 })
  await expect(revisions).toContainText('0.3.27', { timeout: 90_000 })
  await expect(revisions).toContainText('0.3.19')
  await expect(revisions).toContainText('aligned')
  await fs.mkdir(output, { recursive: true })
  await revisions.screenshot({ path: path.join(output, 'builder-runtime-revisions.png') })
  report.cases.push({ id: 'builder_exact_revisions', passed: true })

  await open('applications')
  const catalog = page.locator('[data-webui-widget-id="applications-list"]').filter({ visible: true }).last()
  await expect(catalog).toBeVisible({ timeout: 90_000 })
  await catalog.getByText('AdaOS Builder', { exact: true }).first().click({ timeout: 90_000 })
  const lifecycle = page.locator('[data-webui-widget-id="cbs-lifecycle"]').filter({ visible: true }).last()
  await expect(lifecycle).toBeVisible({ timeout: 60_000 })
  await expect(lifecycle).toContainText('0.3.27', { timeout: 60_000 })
  await expect(lifecycle).toContainText('0.3.19')
  await expect(lifecycle).toContainText('aligned')
  await lifecycle.screenshot({ path: path.join(output, 'applications-runtime-revisions.png') })
  report.cases.push({ id: 'applications_same_revision_authority', passed: true })

  for (const scenario of ['research_workbench', 'taiga_ui_demo_scenario']) {
    await open(scenario)
    await expect(page.locator('[data-webui-widget-id]').filter({ visible: true }).first()).toBeVisible({ timeout: 60_000 })
    const wide = await page.locator('[data-webui-widget-id]').filter({ visible: true }).count()
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(page.locator('[data-webui-widget-id]').filter({ visible: true }).first()).toBeVisible({ timeout: 30_000 })
    const compact = await page.locator('[data-webui-widget-id]').filter({ visible: true }).count()
    report.cases.push({ id: `legacy_ui_${scenario}`, wideWidgets: wide, compactWidgets: compact,
      passed: wide > 0 && compact > 0, scope: 'mount_only_no_application_actions' })
    await page.setViewportSize({ width: 1440, height: 1000 })
  }
  await open('builder')
  report.ok = report.errors.length === 0 && report.cases.every(item => item.passed)
} catch (error) {
  report.errors.push(String(error.message || error).slice(0, 1500))
  if (inspectedPage) {
    report.diagnostic = await inspectedPage.evaluate(() => {
      const widget = window.ng?.getComponent(document.querySelector('ada-details-widget'))
      const state = widget?.state?.getSnapshot?.() || {}
      return { widgets: [...document.querySelectorAll('[data-webui-widget-id]')].map(e => e.getAttribute('data-webui-widget-id')),
        state: Object.fromEntries(['selectedApplicationId', 'detailApplicationId', 'detailOpen', 'activeTab',
          'applicationAttentionStatus', 'applicationAttentionReason'].map(k => [k, state[k]])) }
    })
    await inspectedPage.screenshot({ path: path.join(output, 'beta-reconciliation-failure.png') })
  }
} finally {
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, 'beta-reconciliation-ui.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.ok) process.exitCode = 1
