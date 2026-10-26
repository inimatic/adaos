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
const installLocalBeta = process.env.ADAOS_E2E_INSTALL_LOCAL_BETA === '1'
if (!output || !token || !subnet || !input.host.startsWith('desktop-codex-applications-beta-')) throw new Error('Explicit isolated host and credentials required')
const safe = error => String(error?.message || error).split('\n')[0].replaceAll(token, '[redacted]').slice(0, 400)
const clickCommand = async (page, widgetId, commandId) => {
  const widget = page.locator(`[data-webui-widget-id="${widgetId}"]`).filter({ visible: true })
  const button = widget.locator(`[data-command-id="${commandId}"]`)
  if (!(await button.isVisible())) await widget.getByRole('button', { name: 'More actions', exact: true }).click()
  await button.click({ timeout: 60_000 })
}
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
    const row = { ...test, errors: [], lifecycleReads: [], lifecycleResults: [] }
    page.on('pageerror', error => row.errors.push(safe(error)))
    page.on('request', request => {
      if (!request.url().includes('/root_mcp/call')) return
      try {
        const body = request.postDataJSON()
        if (body.tool_id?.startsWith('applications.')) row.lifecycleReads.push({ tool: body.tool_id,
          application: body.arguments?.application_id })
      } catch {}
    })
    page.on('response', async response => {
      if (!response.url().includes('/root_mcp/call')) return
      try {
        const request = response.request().postDataJSON()
        if (!request.tool_id?.startsWith('applications.')) return
        const value = await response.json()
        const observed = value.response?.result?.lifecycle
        if (observed) row.lifecycleResults.push({ application: observed.application_id,
          version: observed.runtime?.version, admission: observed.runtime?.admission })
        const error = value.response?.error || value.error || value.detail
        if (error) row.errors.push(safe(`${request.tool_id}: ${typeof error === 'string' ? error : JSON.stringify(error)}`))
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
      await page.evaluate(async ({ application, marker }) => {
        const host = [...document.querySelectorAll(`[data-webui-widget-id="${marker}"]`)]
          .find(element => element.getClientRects().length > 0)
        const component = [host, ...host.querySelectorAll('*')]
          .map(element => window.ng.getComponent(element))
          .find(component => component?.actions?.handle && component?.state?.getSnapshot)
        if (!component) throw new Error('Active page action scope is unavailable')
        await component.actions.handle({ type: 'updateState', params: { lifecycleApplicationId: application,
          selectedApplicationId: application, selectedProjectKind: 'project', selectedProjectId: application } })
        await component.actions.handle({ type: 'openModal', params: { modalId: 'core-application-lifecycle',
          statePatch: { lifecycleApplicationId: application, coreLifecyclePlan: {}, coreWriteLossAcknowledged: false } } })
      }, { ...test, marker })
      const identity = page.locator('[data-webui-widget-id="core-lifecycle-identity"]').filter({ visible: true })
      await expect(identity).toBeVisible({ timeout: 90_000 })
      await expect(identity).toContainText(test.version, { timeout: 90_000 })
      if (installLocalBeta && lifecycle.runtime.admission_kind !== 'installed_local_beta') {
        row.stage = 'review-install-local-beta'
        if (!lifecycle.candidate.accepted) throw new Error('Accept the qualified exact Candidate before installation review')
        await clickCommand(page, 'core-lifecycle-review-actions', 'retain-beta')
        const review = page.locator('[data-webui-widget-id="core-lifecycle-plan"]').filter({ visible: true })
        await expect(review).toBeVisible({ timeout: 60_000 })
        await expect(review).toContainText('retain_local_beta', { timeout: 60_000 })
        const operationId = (await review.innerText()).match(/applifecycle\.[0-9a-f]{32}/)?.[0]
        if (!operationId) throw new Error('Review has no exact Core operation identity')
        await clickCommand(page, 'core-lifecycle-confirm', 'apply')
        await expect.poll(async () => {
          const { lifecycle: after } = await mcp('applications.lifecycle.show', { application_id: test.application, webspace_id: input.host })
          return after.runtime.admission_kind
        }, { timeout: 90_000, intervals: [1000, 2000] }).toBe('installed_local_beta')
        row.installedLocalBeta = true
        row.reviewedOperationId = operationId
      }
      await expect(page.locator('[data-webui-widget-id="core-trial-inventory"]').filter({ visible: true })).toBeVisible({ timeout: 60_000 })
      await expect.poll(() => row.lifecycleReads.some(read => read.tool === 'applications.lifecycle.events'),
        { timeout: 35_000, intervals: [500, 1000] }).toBe(true)
      row.clockReadObserved = true
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
