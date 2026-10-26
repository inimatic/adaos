// Read-only mounts of exact selected editions. Never serialize private UI text.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const input = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CASES, 'utf8'))
const output = process.env.ADAOS_E2E_OUTPUT
const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
// Playwright transport errors can append request headers in a multi-line call
// log. Artifacts retain only the safe summary, never that header dump.
const safeError = error => String(error?.message || error).split('\n')[0].replaceAll(token || 'no-token', '[redacted]').slice(0, 500)
const retryTransport = async (operation, row) => {
  for (let attempt = 0; ; attempt++) {
    try { return await operation() } catch (error) {
      if (attempt >= 2 || !/ECONNRESET|socket hang up/.test(String(error.message))) throw error
      row.transportRetries = (row.transportRetries || 0) + 1
      await new Promise(resolve => setTimeout(resolve, 750))
    }
  }
}
if (!input.host.startsWith('desktop-codex-applications-beta-') || !output || !token || !subnet) {
  throw new Error('Explicit isolated acceptance host, output and local credential required')
}
const browser = await chromium.launch({ headless: true })
const report = { ok: false, scope: 'wide_and_compact_read_only_mounts', cases: [], skipped: input.skipped }
const selected = new Set((process.env.ADAOS_E2E_APPLICATIONS || '').split(',').filter(Boolean))
try {
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ token, subnet, hub }) => {
    if (location.href === 'about:blank') return
    for (const [key, value] of Object.entries({ adaos_local_hub_url: hub, adaos_local_hub_token: token,
      adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en' })) {
      localStorage.setItem(key, value)
    }
  }, { token, subnet, hub })
  for (const test of input.cases.filter(test => !selected.size || selected.has(test.application))) {
    const page = await context.newPage()
    const row = { application: test.application, version: test.version, runtime: test.runtime,
      presentationAuthority: test.presentation_authority, errors: [] }
    page.on('pageerror', error => row.errors.push(safeError(error)))
    try {
      row.stage = 'admit'
      const switched = await retryTransport(() => context.request.post(`${hub}/api/node/yjs/webspaces/${input.host}/scenario`, {
        headers: { 'X-AdaOS-Token': token }, data: { scenario_id: test.scenario, wait_for_rebuild: false,
          request_source: 'e2e.applications.beta.reconciliation' }, timeout: 120_000,
      }), row)
      if (!switched.ok() || (await switched.json()).ok !== true) throw new Error(`Scenario admission failed: ${switched.status()}`)
      const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100')
      for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
        webspace_id: input.host, expected_scenario_id: test.scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
      // Real clients attach before observing materialization. An inactive
      // host's stale idempotent snapshot may require that attachment to repair.
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      row.stage = 'materialize'
      await expect.poll(async () => {
        const response = await retryTransport(() => context.request.get(`${hub}/api/node/yjs/webspaces/${input.host}/materialization?include_runtime=0&verify_live=1`, {
          headers: { 'X-AdaOS-Token': token }, timeout: 30_000 }), row)
        const state = await response.json()
        row.materialization = { ready: state.materialization?.ready, stale: state.materialization?.stale,
          scenario: state.materialization?.current_scenario, status: state.rebuild?.status }
        if (state.rebuild?.status === 'failed') throw new Error(`Materialization failed: ${state.rebuild.error}`)
        return !state.rebuild?.pending && state.materialization?.ready && !state.materialization?.stale
          && state.materialization?.current_scenario === test.scenario
      }, { timeout: 180_000, intervals: [500, 1000, 2000] }).toBe(true)
      row.stage = 'navigate'
      if (test.modal) {
        await page.waitForFunction(() => Boolean(document.querySelector('ada-chat-widget')), { timeout: 90_000 })
        await page.evaluate(async ({ skill, modal, navigation }) => {
          const component = window.ng?.getComponent(document.querySelector('ada-chat-widget'))
          if (!component?.actions?.handle) throw new Error('Public action handler is unavailable')
          await component.actions.handle({ type: 'navigate', params: { to: navigation || `${skill}.${modal}`, surface: 'modal', modalId: modal } })
        }, test)
      }
      const selector = test.widgets.map(id => `[data-webui-widget-id=${JSON.stringify(id)}]`).join(',')
      const authored = page.locator(selector).filter({ visible: true })
      row.stage = 'wide'
      await expect(authored.first()).toBeVisible({ timeout: 90_000 })
      row.wideWidgets = await authored.count()
      await page.setViewportSize({ width: 390, height: 844 })
      row.stage = 'compact'
      await expect(authored.first()).toBeVisible({ timeout: 30_000 })
      row.compactWidgets = await authored.count()
      if (test.specimensWidgets?.length) {
        row.stage = 'specimens-wide'
        await page.setViewportSize({ width: 1440, height: 1000 })
        await page.locator('[data-webui-widget-id="demo_metric_actions"] [data-command-id="open-component-specimens"]').click()
        const specimens = page.locator(test.specimensWidgets.map(id => `[data-webui-widget-id=${JSON.stringify(id)}]`).join(','))
          .filter({ visible: true })
        await expect(specimens.first()).toBeVisible({ timeout: 30_000 })
        row.specimensWideWidgets = await specimens.count()
        row.stage = 'specimens-compact'
        await page.setViewportSize({ width: 390, height: 844 })
        await expect(specimens.first()).toBeVisible({ timeout: 30_000 })
        row.specimensCompactWidgets = await specimens.count()
      }
      row.ok = row.wideWidgets > 0 && row.compactWidgets > 0 && row.errors.length === 0
    } catch (error) {
      row.ok = false
      row.errors.push(safeError(error))
      row.expectedWidgets = test.widgets
      row.visibleWidgetIds = await page.locator('[data-webui-widget-id]').filter({ visible: true })
        .evaluateAll(elements => [...new Set(elements.map(element => element.getAttribute('data-webui-widget-id')))])
      try {
        const response = await context.request.get(`${hub}/api/node/yjs/webspaces/${input.host}/materialization/snapshot?include_runtime=0&scope=essential`, {
          headers: { 'X-AdaOS-Token': token }, timeout: 30_000 })
        const observed = await response.json()
        const ui = observed.snapshot?.ui || {}
        row.serverScenario = ui.current_scenario
        row.serverWidgetIds = (ui.application?.desktop?.pageSchema?.widgets || []).map(widget => widget.id)
      } catch (diagnosticError) { row.diagnosticError = safeError(diagnosticError) }
    } finally {
      await page.close()
      report.cases.push(row)
      await fs.mkdir(output, { recursive: true })
      await fs.writeFile(path.join(output, 'application-mounts.json'), JSON.stringify(report, null, 2))
      console.log(JSON.stringify(row))
    }
  }
  report.ok = report.cases.length > 0 && report.cases.every(row => row.ok)
} finally {
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, 'application-mounts.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
if (!report.ok) process.exitCode = 1
