import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

if (process.env.ENV_TYPE !== 'dev') throw new Error('Interaction review requires ENV_TYPE=dev')
const checkpointPath = path.resolve(process.env.ADAOS_E2E_CHECKPOINT || '')
const checkpoint = JSON.parse(await fs.readFile(checkpointPath, 'utf8'))
const ownership = checkpoint.cleanup
if (!ownership?.test || ownership.status !== 'retained_for_review' || ownership.acceptance !== 'not_approved') {
  throw new Error('Only retained, unapproved test artifacts may be mutated')
}
const created = checkpoint.steps.find(step => step.id === 'create')?.output
const scenario = created?.scenario_id
if (!ownership.owned_artifacts.some(item => item.primary_ref === `scenario:${scenario}` && item.project_id === scenario)) {
  throw new Error('Checkpoint does not own the scenario')
}
const preview = ownership.previews.find(item => item.scenario_id === scenario && item.test && item.stage === 'prototype')
if (!preview?.webspace_id) throw new Error('Owned preview is missing')
const source = path.resolve(created.artifact_root)
const webui = JSON.parse(await fs.readFile(path.join(source, 'webui.json'), 'utf8'))
const application = webui.ui.application
const widgets = application.desktop.pageSchema.widgets
const forms = [
  ...widgets.filter(widget => widget.type === 'ui.form').map(widget => ({ widget })),
  ...Object.entries(application.modals || {}).flatMap(([modalId, modal]) =>
    (modal.schema?.widgets || []).filter(widget => widget.type === 'ui.form').map(widget => ({ widget, modalId }))),
]
const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
if (!token || !subnet) throw new Error('Explicit subnet and local token are required')
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || path.join(path.dirname(checkpointPath), 'interactions'))
await fs.mkdir(output, { recursive: true })
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: preview.webspace_id, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
const browser = await chromium.launch({ headless: true })
const report = { scenario, checkpoint: checkpointPath, samples: [], passed: false }
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-interactions', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace: preview.webspace_id })
    const page = await context.newPage()
    const sample = { layout, checks: [], errors: [], mutations: [] }
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(error.message))
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/resources/operate') {
        const body = request.postDataJSON()
        sample.mutations.push({ resource: body.resource_type, operation: body.operation_id, record: body.record_id, payload: body.payload })
      }
    })
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync.materialization.currentScenario === expected
      }, scenario, { timeout: 60_000 })
      for (const { widget, modalId } of forms) {
        const update = widget.actions?.find(action => action.type === 'resourceOperation' && action.params?.operation_id === 'update')
        const field = widget.inputs.fields?.find(field => ['shortText', 'longText'].includes(field.type) && update?.params?.payload?.[field.id] === `$event.values.${field.id}` && !field.visibleIf)
        const collection = widgets.find(item => ['ui.table', 'ui.list'].includes(item.type) && item.dataSource?.resourceType === update?.target)
        if (!update || !field || !collection) {
          sample.checks.push({ editor: widget.id, status: 'not_exercised', reason: 'No supported text update and matching collection' })
          continue
        }
        const row = host(collection.id).locator('tr.row-selectable, .collection-focus-item').first()
        await expect(row).toBeVisible({ timeout: 30_000 })
        await row.click()
        if (modalId) await host(`open-${widget.id}`).locator('[data-command-id="edit"]').click()
        const form = host(widget.id)
        const input = form.locator(`[id=${JSON.stringify(`form-${widget.id}-${field.id}`)}]`).locator('input, textarea').or(form.locator(`input[id=${JSON.stringify(`form-${widget.id}-${field.id}`)}], textarea[id=${JSON.stringify(`form-${widget.id}-${field.id}`)}]`)).first()
        await expect(input).toBeVisible({ timeout: 15_000 })
        await expect(input).toBeEditable({ timeout: 30_000 })
        const original = await input.inputValue()
        const marker = `review-${checkpoint.run_id}-${layout}`
        await input.fill(marker)
        const button = form.locator(`[data-command-id=${JSON.stringify(update.id)}]`)
        await expect(button).toBeEnabled()
        await page.screenshot({ path: path.join(output, `${layout}-${widget.id}-edit.png`), fullPage: true })
        sample.beforeSubmit = await form.evaluate((element, fieldId) => {
          const component = window.ng?.getComponent(element.querySelector('ada-form-widget'))
          return { field: fieldId, values: component?.values, input: element.querySelector('input,textarea')?.value,
            actions: component?.widget?.actions, selectedRecordId: component?.selectedRecordId }
        }, field.id)
        const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/resources/operate', { timeout: 15_000 })
        await button.click()
        if (update.confirmation) await page.locator('ion-alert').last().locator('button').last().click()
        const response = await responsePromise
        const result = await response.json()
        if (!response.ok() || result.ok === false) throw new Error(`Update rejected: ${JSON.stringify(result)}`)
        const mutation = sample.mutations.at(-1)
        if (!mutation?.record || mutation.payload[field.id] !== marker) throw new Error('Wrong record or payload was submitted')
        if (modalId) {
          await expect(page.locator('ion-modal').filter({ has: form })).toHaveCount(0)
          await host(`open-${widget.id}`).locator('[data-command-id="edit"]').click()
        } else await page.reload({ waitUntil: 'domcontentloaded' })
        // Re-open and verify the persisted value, not merely the edited DOM.
        if (!modalId) {
          await page.waitForFunction(expected => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === expected, scenario, { timeout: 60_000 })
          await row.click()
        }
        await expect(input).toHaveValue(marker, { timeout: 30_000 })
        await input.fill(original)
        const restore = page.waitForResponse(response => new URL(response.url()).pathname === '/api/resources/operate', { timeout: 15_000 })
        await button.click()
        if (update.confirmation) await page.locator('ion-alert').last().locator('button').last().click()
        const restored = await restore
        if (!restored.ok() || (await restored.json()).ok === false) throw new Error('Could not restore fixture')
        sample.checks.push({ editor: widget.id, status: 'passed', task: 'select/edit/save/reopen/restore', surface: modalId ? 'overlay' : 'inline' })
      }
      if (!sample.checks.some(check => check.status === 'passed')) throw new Error('No mutation exercised; this is not a task pass')
      await page.screenshot({ path: path.join(output, `${layout}-complete.png`), fullPage: true })
    } catch (error) {
      sample.failure = error.message
      sample.text = await page.locator('body').innerText()
      await page.screenshot({ path: path.join(output, `${layout}-failure.png`), fullPage: true })
    } finally { await context.close() }
  }
} finally { await browser.close() }
report.passed = report.samples.length === 2 && report.samples.every(sample => !sample.failure && !sample.errors.length)
await fs.writeFile(path.join(output, 'review.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify(report))
if (!report.passed) process.exitCode = 1
