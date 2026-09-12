import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

if (process.env.ENV_TYPE !== 'dev') throw new Error('Command probes require ENV_TYPE=dev')
const checkpointPath = path.resolve(process.env.ADAOS_E2E_CHECKPOINT)
const checkpoint = JSON.parse(await fs.readFile(checkpointPath, 'utf8'))
const created = checkpoint.steps.find(step => step.id === 'create')?.output
const scenario = created?.scenario_id
const ownership = checkpoint.cleanup
if (!ownership?.test || ownership.acceptance !== 'not_approved'
  || !ownership.owned_artifacts.some(item => item.primary_ref === `scenario:${scenario}` && item.project_id === scenario)) {
  throw new Error('Only checkpoint-owned, unapproved test prototypes can be mutated')
}
const preview = ownership.previews.find(item => item.scenario_id === scenario && item.test)
if (!preview) throw new Error('No owned preview')
const application = JSON.parse(await fs.readFile(path.join(created.artifact_root, 'webui.json'), 'utf8')).ui.application
const widgets = application.desktop.pageSchema.widgets
const forms = [...widgets.filter(item => item.type === 'ui.form').map(widget => ({ widget })),
  ...Object.entries(application.modals || {}).flatMap(([modalId, modal]) =>
    (modal.schema?.widgets || []).filter(item => item.type === 'ui.form').map(widget => ({ widget, modalId })))]
const declaredDeleteTargets = new Set(forms.flatMap(({ widget }) => (widget.actions || [])
  .filter(action => action.type === 'resourceOperation' && action.params?.operation_id === 'delete')
  .map(action => action.target)))
// Create lookup records before their dependants. Cycles keep their authored order.
const orderedForms = []
const pendingForms = [...forms]
while (pendingForms.length) {
  const index = pendingForms.findIndex(({ widget }) => !(widget.inputs.fields || []).some(field =>
    field.optionsDataSource && pendingForms.some(other => other.widget !== widget
      && other.widget.dataSource?.resourceType === field.optionsDataSource.resourceType)))
  orderedForms.push(...pendingForms.splice(Math.max(index, 0), 1))
}
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8778'
const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
if (!token || !subnet) throw new Error('Explicit local token and subnet required')
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || path.join(path.dirname(checkpointPath), 'commands'))
await fs.mkdir(output, { recursive: true })
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: preview.webspace_id, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
const report = { scenario, checkpoint: checkpointPath, samples: [] }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace, locale }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-command-probe', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: locale })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace: preview.webspace_id, locale: checkpoint.context.locale })
    const page = await context.newPage()
    page.setDefaultTimeout(20_000)
    const sample = { layout, checks: [], fixtureCleanup: [], errors: [] }
    const freshRecords = new Map()
    const createdReceipts = []
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(error.message))
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
    const state = form => form.evaluate(element => {
      const component = window.ng?.getComponent(element.querySelector('ada-form-widget'))
      return { record: component?.recordValues, values: component?.values }
    })
    const open = async (widget, modalId, collection, create = false, selectedRow) => {
      if (create) return host(`open-${widget.id}`).locator('[data-command-id="new"]').click()
      const row = selectedRow || host(collection.id).locator('tr.row-selectable, .collection-focus-item').first()
      await row.click()
      if (!modalId) return
      for (const owner of widgets) {
        const action = owner.actions?.find(item => item.type === 'openModal' && item.params?.modalId === modalId
          && item.on?.startsWith('click:') && item.on !== 'click:new')
        if (action) {
          const id = owner.type === 'ui.actions' ? action.on.slice(6) : action.id || action.on
          await host(owner.id).locator(`[data-command-id=${JSON.stringify(id)}]`).click()
          break
        }
      }
    }
    const submit = async (form, action) => {
      const response = page.waitForResponse(item => new URL(item.url()).pathname === '/api/resources/operate')
      void response.catch(() => {})
      await form.locator(`[data-command-id=${JSON.stringify(action.id)}]`).click()
      if (action.confirmation) await page.locator('ion-alert').last().locator('button').last().click()
      const received = await response
      const result = await received.json()
      if (!received.ok() || result.ok === false) throw new Error(`Command rejected: ${JSON.stringify(result)}`)
      return { body: received.request().postDataJSON(), headers: await received.request().allHeaders(), result }
    }
    const clean = async (receipt, payload, operation = 'update', recordId = receipt.body.record_id) => {
      if (!receipt.body.resource_type?.startsWith('prototype.') || !recordId) throw new Error('Unsafe fixture cleanup')
      const headers = Object.fromEntries(Object.entries(receipt.headers).filter(([key]) => !['content-length', 'host', 'origin'].includes(key)))
      const response = await context.request.post(`${hub}/api/resources/operate`, {
        headers, data: { ...receipt.body, operation_id: operation, record_id: recordId, payload },
      })
      const result = await response.json()
      const ok = response.ok() && result.ok !== false
      sample.fixtureCleanup.push({ resource: receipt.body.resource_type, recordId, operation, ok })
      if (!ok) throw new Error(`Fixture cleanup failed: ${JSON.stringify(result)}`)
    }
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === expected, scenario, { timeout: 60_000 })
      for (const { widget, modalId } of orderedForms) {
        const collection = widgets.find(item => ['ui.table', 'ui.list'].includes(item.type)
          && item.dataSource?.resourceType === widget.dataSource?.resourceType)
        if (!collection) continue
        const fixed = widget.actions.filter(action => action.type === 'resourceOperation' && action.params.operation_id === 'update'
          && Object.values(action.params.payload).every(value => !String(value).startsWith('$')))
        for (const action of fixed) {
          await open(widget, modalId, collection)
          const form = host(widget.id)
          await expect.poll(async () => (await state(form)).record?.id).toBeTruthy()
          const original = (await state(form)).record
          let receipt
          try {
            receipt = await submit(form, action)
            if (receipt.body.record_id !== original.id) throw new Error('Mutation targeted another record')
            if (modalId) await expect(form).toHaveCount(0)
            await open(widget, modalId, collection)
            await expect.poll(async () => {
              const record = (await state(form)).record
              return record?.id === original.id && Object.entries(action.params.payload).every(([key, value]) => record[key] === value)
            }).toBe(true)
            sample.checks.push({ command: action.id, task: 'select/transition/reopen', passed: true })
          } finally {
            if (receipt) await clean(receipt, Object.fromEntries(Object.keys(action.params.payload).map(key => [key, original[key]])))
            if (modalId && await form.isVisible()) await page.locator('ion-modal').last().getByRole('button', { name: /Close|Закрыть/, exact: true }).click()
          }
        }
        const create = widget.actions.find(action => action.type === 'resourceOperation' && action.params.operation_id === 'create')
        if (!create) continue
        if (widget.inputs.fields.some(field => field.required && field.type === 'fileUpload')) {
          sample.checks.push({ command: create.id, task: 'create', status: 'not_exercised', reason: 'Required upload needs separate probe' })
          continue
        }
        await open(widget, modalId, collection, true)
        const form = host(widget.id)
        await expect(form).toBeVisible()
        const marker = `probe-${layout}-${Date.now()}`
        const chosenRelations = []
        for (const field of widget.inputs.fields) {
          const container = form.locator(`[data-webui-field-id=${JSON.stringify(field.id)}]`)
          if (!await container.isVisible()) continue
          if (field.readOnly) {
            for (const control of await container.locator('input,textarea,select').all()) await expect(control).toBeDisabled()
            continue
          }
          if (field.type === 'dropdown') {
            const select = container.locator('select')
            await expect(select).toBeEnabled()
            const related = freshRecords.get(field.optionsDataSource?.resourceType)
            const options = () => form.evaluate((element, fieldId) => {
              const component = window.ng?.getComponent(element.querySelector('ada-form-widget'))
              const field = component?.fields.find(item => item.id === fieldId)
              return field ? component.choiceOptions(field) : []
            }, field.id)
            const expected = related?.[field.optionValuePath || 'id']
            if (related) await expect.poll(async () => (await options()).some(option => option.value === expected)).toBe(true)
            const choices = await options()
            const index = related ? choices.findIndex(option => option.value === expected) : 0
            if (!choices.length) throw new Error(`No selectable option for ${field.id}`)
            await select.selectOption({ index: index + 1 })
            if (related) chosenRelations.push({ field: field.id, value: expected, label: choices[index].label })
          } else if (['singleChoice', 'multiChoice'].includes(field.type)) {
            await container.locator('input[type=radio],input[type=checkbox]').first().check()
          } else if (['shortText', 'longText'].includes(field.type)) {
            await container.locator('input,textarea').fill(marker)
          } else if (['number', 'integer'].includes(field.type)) {
            await container.locator('input').fill('12')
          } else if (field.type === 'date') await container.locator('input').fill('2026-09-11')
          else if (field.type === 'time') await container.locator('input').fill('12:30')
        }
        const receipt = await submit(form, create)
        for (const relation of chosenRelations) {
          if (receipt.body.payload[relation.field] !== relation.value) throw new Error(`Wrong related identity submitted for ${relation.field}`)
          sample.checks.push({ command: create.id, task: 'create-related/select/save', passed: true, ...relation })
        }
        sample.checks.push({ command: create.id, task: 'fill/create', passed: true, submittedFields: Object.keys(receipt.body.payload) })
        if (modalId) await expect(form).toHaveCount(0)
        const saved = receipt.result.result?.record
        if (saved) {
          if (!saved.id || saved.id !== receipt.result.result.record_id) throw new Error('Inconsistent created identity')
          freshRecords.set(receipt.body.resource_type, saved)
          createdReceipts.push({ receipt, saved })
          // Identity, not a marker in an optional text field, locates lookup-only forms' records.
          const rows = host(collection.id).locator('tr.row-selectable, .collection-focus-item')
          const rowIndex = () => rows.evaluateAll((elements, id) => elements.findIndex(element =>
            window.ng?.getContext(element)?.$implicit?.id === id), saved.id)
          await expect.poll(rowIndex).toBeGreaterThanOrEqual(0)
          const row = rows.nth(await rowIndex())
          await expect(row).toBeVisible()
          if (!(await row.innerText()).trim()) throw new Error('Created record renders an empty row')
          sample.checks.push({ command: create.id, task: 'created-record-visible', passed: true, recordId: saved.id })
          if (chosenRelations.length && widget.actions.some(action => action.type === 'resourceOperation' && action.params.operation_id === 'update')) {
            await open(widget, modalId, collection, false, row)
            await expect.poll(async () => {
              const record = (await state(form)).record
              return record?.id === saved.id && chosenRelations.every(relation => record[relation.field] === relation.value)
            }).toBe(true)
            sample.checks.push({ command: create.id, task: 'related-record/reopen', passed: true })
            if (modalId) await page.locator('ion-modal').last().getByRole('button', { name: /Close|Закрыть/, exact: true }).click()
          }
        } else sample.fixtureCleanup.push({ operation: 'delete', ok: false, reason: 'Created identity not present in operation receipt; test record retained' })
      }
      if (!sample.checks.some(check => check.passed)) throw new Error('No commands exercised')
      await page.screenshot({ path: path.join(output, `${layout}.png`), fullPage: true })
    } catch (error) {
      sample.failure = error.message
      sample.text = await page.locator('body').innerText({ timeout: 2000 }).catch(() => 'Diagnostics unavailable')
      await page.screenshot({ path: path.join(output, `${layout}-failure.png`), fullPage: true, timeout: 5000 }).catch(() => {})
    } finally {
      // Do not delete parents of retained children or mutate undeclared operations.
      const canClean = createdReceipts.every(({ receipt }) => declaredDeleteTargets.has(receipt.body.resource_type))
      for (const { receipt, saved } of createdReceipts.reverse()) {
        if (canClean) await clean(receipt, {}, 'delete', saved.id).catch(error => { sample.cleanupFailure = error.message })
        else sample.fixtureCleanup.push({ resource: receipt.body.resource_type, recordId: saved.id,
          operation: 'retain', status: 'retained', reason: 'Related test fixtures retained because not all resources declare deletion' })
      }
    }
    await context.close()
    await fs.writeFile(path.join(output, 'commands.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
  }
} finally { await browser.close() }
report.passed = report.samples.length === 2 && report.samples.every(sample => !sample.failure && !sample.cleanupFailure && !sample.errors.length)
await fs.writeFile(path.join(output, 'commands.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify(report))
if (!report.passed) process.exitCode = 1
