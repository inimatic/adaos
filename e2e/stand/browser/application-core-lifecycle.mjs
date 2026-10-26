// Read canonical state in all three real consumers. No uninstall, deletion,
// provider request or Candidate decision is performed by this browser test.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const input = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CASES, 'utf8'))
const output = process.env.ADAOS_E2E_OUTPUT
const token = process.env.ADAOS_E2E_HUB_TOKEN
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const subnet = process.env.ADAOS_E2E_SUBNET_ID
if (!output || !token || !subnet || !input.host.startsWith('desktop-codex-applications-beta-')) throw new Error('Explicit isolated host and credentials required')
const safe = error => String(error?.message || error).split('\n')[0].replaceAll(token, '[redacted]').slice(0, 400)
const browser = await chromium.launch({ headless: true })
const report = { ok: false, scope: 'three_consumers_core_runtime_and_trials', cases: [], modelCalls: 0, destructiveActions: 0 }
try {
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ token, subnet, hub }) => {
    if (location.href === 'about:blank') return
    for (const [key, value] of Object.entries({ adaos_local_hub_url: hub, adaos_local_hub_token: token,
      adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en' })) localStorage.setItem(key, value)
  }, { token, subnet, hub })
  const mcp = async (tool, args, dryRun = true) => {
    const response = await context.request.post(`${hub}/api/admin/root_mcp/call`, {
      headers: { 'X-AdaOS-Token': token }, data: { tool_id: tool, arguments: args, dry_run: dryRun,
        scope: { webspace_id: input.host } }, timeout: 90_000 })
    if (!response.ok()) throw new Error(`${tool}: HTTP ${response.status()}`)
    const value = await response.json()
    const envelope = value.response || value
    if (envelope.ok === false || envelope.error) throw new Error(`${tool}: ${envelope.error?.code || 'tool rejected'}`)
    return envelope.result || {}
  }
  const selected = new Set((process.env.ADAOS_E2E_APPLICATIONS || '').split(',').filter(Boolean))
  const cases = input.cases.filter(test => !selected.size || selected.has(test.application))
  for (const test of cases) {
    const page = await context.newPage()
    const row = { ...test, errors: [], lifecycleReads: [] }
    page.on('pageerror', error => row.errors.push(safe(error)))
    page.on('request', request => {
      if (!request.url().includes('/root_mcp/call')) return
      try {
        const body = request.postDataJSON()
        if (body.tool_id?.startsWith('applications.lifecycle.')) row.lifecycleReads.push({ tool: body.tool_id,
          application: body.arguments?.application_id })
      } catch {}
    })
    try {
      row.stage = 'core-observation'
      const { lifecycle } = await mcp('applications.lifecycle.show', { application_id: test.application, webspace_id: input.host })
      if (lifecycle?.runtime?.version !== test.version) throw new Error('Selected Core version differs from qualified package')
      row.core = { version: lifecycle.runtime.version, admission: lifecycle.runtime.admission,
        candidate: lifecycle.candidate.status, admissionKind: lifecycle.runtime.admission_kind, schema: lifecycle.schema }
      row.stage = 'mount'
      const response = await context.request.post(`${hub}/api/node/yjs/webspaces/${input.host}/scenario`, {
        headers: { 'X-AdaOS-Token': token }, data: { scenario_id: test.scenario, wait_for_rebuild: false,
          request_source: 'e2e.core.lifecycle' }, timeout: 90_000 })
      if (!response.ok()) throw new Error(`Scenario admission: HTTP ${response.status()}`)
      const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100')
      for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
        webspace_id: input.host, expected_scenario_id: test.scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await page.waitForFunction(() => {
        const element = document.querySelector('ada-details-widget')
        return Boolean(element && window.ng?.getComponent(element)?.actions?.handle)
      }, undefined, { timeout: 120_000 })
      await expect.poll(async () => {
        const response = await context.request.get(`${hub}/api/node/yjs/webspaces/${input.host}/materialization?include_runtime=0&verify_live=1`, {
          headers: { 'X-AdaOS-Token': token }, timeout: 30_000 })
        const state = await response.json()
        return state.materialization?.ready && !state.materialization?.stale && state.materialization?.current_scenario === test.scenario
      }, { timeout: 180_000, intervals: [500, 1000, 2000] }).toBe(true)
      const marker = { applications: 'catalog-tabs', web_desktop: 'primary-nav', adaos_builder: 'design-workbench-header' }[test.application]
      await expect(page.locator(`[data-webui-widget-id="${marker}"]`).filter({ visible: true })).toBeVisible({ timeout: 90_000 })
      row.stage = 'core-modal'
      await page.evaluate(async ({ application }) => {
        const component = window.ng.getComponent(document.querySelector('ada-details-widget'))
        await component.actions.handle({ type: 'updateState', params: { lifecycleApplicationId: application,
          selectedApplicationId: application, selectedProjectKind: 'project', selectedProjectId: application } })
        await component.actions.handle({ type: 'openModal', params: { modalId: 'core-application-lifecycle' } })
      }, test)
      const identity = page.locator('[data-webui-widget-id="core-lifecycle-identity"]').filter({ visible: true })
      await expect(identity).toBeVisible({ timeout: 90_000 })
      await expect(identity).toContainText(test.version, { timeout: 90_000 })
      await expect(page.locator('[data-webui-widget-id="core-trial-inventory"]').filter({ visible: true })).toBeVisible({ timeout: 60_000 })
      row.wide = true
      await page.setViewportSize({ width: 390, height: 844 })
      await expect(identity).toBeVisible({ timeout: 30_000 })
      row.compact = true
      row.stage = 'history'
      const history = await mcp('applications.lifecycle.operations', { application_id: test.application, limit: 10 })
      const inventory = await mcp('applications.lifecycle.trials', { application_id: test.application, limit: 10 })
      row.historyCount = history.operations?.length || 0
      row.inventoryCount = inventory.trials?.length || 0
      row.ok = row.wide && row.compact && row.inventoryCount > 0 && row.errors.length === 0
    } catch (error) {
      row.ok = false; row.error = safe(error)
      row.dom = await page.evaluate(() => ({ path: location.pathname, ng: Boolean(window.ng),
        customElements: [...new Set([...document.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).filter(t => t.includes('-')))].slice(0, 35) }))
      row.identity = await page.locator('[data-webui-widget-id="core-lifecycle-identity"]').filter({ visible: true }).first().innerText().catch(() => '')
    }
    report.cases.push(row)
    console.log(JSON.stringify(row))
    await page.close()
  }
  report.ok = report.cases.length === cases.length && report.cases.every(row => row.ok)
} finally {
  await browser.close()
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, 'core-lifecycle-ui.json'), JSON.stringify(report, null, 2))
}
if (!report.ok) process.exitCode = 1
