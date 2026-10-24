// Mutate only the explicitly owned, disposable Core AI acceptance Prototype.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const project = process.env.ADAOS_E2E_PROJECT
const host = process.env.ADAOS_E2E_PREVIEW_HOST
const output = process.env.ADAOS_E2E_OUTPUT
const sourcePath = process.env.ADAOS_E2E_EXPECTED_WEBUI
if (project !== 'ai_core_full_prototype_scoped' || host !== 'ai-core-builder-generation-scoped-dev'
  || !output || !sourcePath || !process.env.ADAOS_E2E_HUB_TOKEN) throw new Error('Explicit owned fixture required')
const source = JSON.parse(await fs.readFile(sourcePath, 'utf8'))
const app = source.ui.application
const table = app.desktop.pageSchema.widgets.find(item => item.type === 'ui.table')
const resource = table?.dataSource?.resourceType
if (!resource?.startsWith(`prototype.project.${project}.`)) throw new Error('Unscoped Prototype resource rejected')
const forms = Object.entries(app.modals || {}).flatMap(([modalId, modal]) =>
  (modal.schema?.widgets || []).filter(item => item.type === 'ui.form').map(widget => ({ widget, modalId })))
const create = forms.find(({ widget }) => widget.actions?.some(action => action.params?.operation_id === 'create'))
const complete = forms.find(({ widget }) => widget.actions?.some(action => action.params?.payload?.status === 'done'))
if (!create || !complete) throw new Error('Create and Complete forms required')
const createAction = create.widget.actions.find(action => action.params?.operation_id === 'create')
const completeAction = complete.widget.actions.find(action => action.params?.payload?.status === 'done')
const titleFields = (create.widget.inputs?.fields || []).filter(field => field.type === 'shortText'
  && !field.readOnly && createAction.params?.payload?.[field.id] === `$event.values.${field.id}`)
if (titleFields.length !== 1) throw new Error('Exactly one authored task-title input required')
const report = { ok: false, providerCalls: 0, project, resource, samples: [] }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const sample = { layout, errors: [] }
    report.samples.push(sample)
    const context = await browser.newContext({ viewport, locale: 'en-US' })
    await context.addInitScript(({ token, subnet }) => {
      for (const [key, value] of Object.entries({ adaos_local_hub_url: 'http://127.0.0.1:8777',
        adaos_local_hub_token: token, adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo',
        adaos_last_used_zone: 'lo', adaos_lang: 'en' })) localStorage.setItem(key, value)
    }, { token: process.env.ADAOS_E2E_HUB_TOKEN, subnet: process.env.ADAOS_E2E_SUBNET_ID })
    const page = await context.newPage()
    page.setDefaultTimeout(30_000)
    page.on('pageerror', error => sample.errors.push(String(error)))
    await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
      await page.locator('.component-updates-panel__tools button').last().dispatchEvent('click')
    })
    const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100')
    for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo',
      subnet_id: process.env.ADAOS_E2E_SUBNET_ID, webspace_id: host,
      expected_scenario_id: project, try_local_hub: '1' })) url.searchParams.set(key, value)
    const widget = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).filter({ visible: true }).last()
    const command = (parent, id) => parent.locator(`[data-command-id=${JSON.stringify(id)}]`).first()
    const operationResponse = () => page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/resources/operate'
      && response.request().postDataJSON()?.resource_type === resource)
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      const collection = widget(table.id)
      await expect(collection).toBeVisible({ timeout: 60_000 })
      await expect(collection.getByText('Read SDK guide', { exact: true })).toBeVisible()
      await expect(collection.getByText('Test shared connection', { exact: true })).toBeVisible()
      await command(collection, createAction.id).click()
      const form = widget(create.widget.id)
      await expect(form).toBeVisible()
      const titleField = `[data-webui-field-id=${JSON.stringify(titleFields[0].id)}]`
      const title = form.locator(`${titleField} input, ${titleField} textarea`)
      const marker = `Core acceptance ${layout} ${Date.now()}`
      await title.fill(marker)
      const createdResponse = operationResponse()
      await command(form, createAction.id).click()
      const created = await createdResponse
      const createdBody = await created.json()
      if (!created.ok() || !createdBody.ok) throw new Error('Create operation rejected')
      const recordId = createdBody.result?.record_id
      if (!recordId) throw new Error('Create receipt has no record identity')
      await expect(form).toHaveCount(0)
      const row = collection.locator('tr.row-selectable, .collection-focus-item').filter({ hasText: marker }).first()
      await expect(row).toBeVisible()
      await expect(row).toContainText('Open')
      await command(row, completeAction.id).click()
      const completion = widget(complete.widget.id)
      await expect(completion).toBeVisible()
      const completedResponse = operationResponse()
      await command(completion, completeAction.id).click()
      if (completeAction.confirmation) {
        const confirmation = page.locator('ion-alert').last()
        await expect(confirmation).toBeVisible()
        await confirmation.locator('button').last().click()
      }
      const completed = await completedResponse
      const request = completed.request().postDataJSON()
      const completedBody = await completed.json()
      if (!completed.ok() || !completedBody.ok || request.record_id !== recordId
        || completedBody.result?.record?.status !== 'done') throw new Error('Complete did not update the created record')
      await expect(completion).toHaveCount(0)
      await expect(row).toContainText('Done')
      sample.created = true
      sample.completed = true
      sample.recordId = recordId
      sample.retainedDisposableRecord = true
      await fs.mkdir(output, { recursive: true })
      await collection.screenshot({ path: path.join(output, `generated-${layout}.png`) })
    } catch (error) {
      sample.errors.push(String(error))
    } finally {
      await context.close()
    }
  }
  report.ok = report.samples.every(sample => sample.created && sample.completed && !sample.errors.length)
} finally {
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, 'generated-prototype-ui.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.ok) process.exitCode = 1
