// Task-grounded qualification. Never included in model context.
import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { dismissSystemOverlays, installSystemOverlayDismissal, prototypeRowById, revealPrototypeWidget, selectPrototypeFormOption, selectPrototypeRow } from './prototype-navigation.mjs'
import { directRequestCompositionFields, transitionPersistenceExpectations } from './prototype-procurement-fields.mjs'
import { qualifyBasicSupplierComparison, supplierComparisonMetadata } from './prototype-procurement-basic.mjs'

const expect = baseExpect.configure({ timeout: 15_000 })

if (process.env.ENV_TYPE !== 'dev') throw new Error('Requires DEV')
const checkpoint = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CHECKPOINT, 'utf8'))
const created = checkpoint.steps.find(step => step.id === 'create')?.output
const scenario = created?.scenario_id
const ownership = checkpoint.cleanup
if (!ownership?.test || ownership.acceptance !== 'not_approved' || !scenario?.startsWith('test_')
  || !ownership.owned_artifacts.some(item => item.primary_ref === `scenario:${scenario}` && item.project_id === scenario)) {
  throw new Error('Only owned unapproved TEST applications may be mutated')
}
const preview = ownership.previews.find(item => item.scenario_id === scenario && item.test && item.stage === 'prototype')
if (!preview?.webspace_id) throw new Error('Owned Preview required')
const source = created.artifact_root
const application = JSON.parse(await fs.readFile(path.join(source, 'webui.json'), 'utf8')).ui.application
const semantic = JSON.parse(await fs.readFile(path.join(source, 'semantic.webui.json'), 'utf8'))
const widgets = application.desktop.pageSchema.widgets
const forms = [...widgets.filter(widget => widget.type === 'ui.form'),
  ...Object.values(application.modals || {}).flatMap(modal => modal.schema?.widgets || []).filter(widget => widget.type === 'ui.form')]
const transition = semantic.commands.find(command => command.kind === 'transition'
  && /approval|pending|согласован/i.test(JSON.stringify(command)))
const requestEditor = semantic.views.find(view => view.id === transition?.view_ref)
const createCommand = semantic.commands.find(command => command.kind === 'create'
  && semantic.views.find(view => view.id === command.view_ref)?.resource_ref === requestEditor?.resource_ref)
const createForm = forms.find(form => form.id === createCommand?.view_ref)
const createAction = createForm?.actions?.find(action => action.id === createCommand?.id)
const transitionForm = forms.find(form => form.id === transition?.view_ref)
const transitionAction = transitionForm?.actions?.find(action => action.id === transition?.id)
const collection = widgets.find(widget => ['ui.table', 'ui.list', 'collection.board'].includes(widget.type)
  && widget.dataSource?.resourceType === createAction?.target)
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output, { recursive: true })
const hub = process.env.ADAOS_E2E_HUB_URL
const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
if (!hub || !token || !subnet) throw new Error('Explicit hub, token and subnet required')
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: preview.webspace_id, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
const report = { scenario, scope: 'supplementary create request and submit for approval; not a replacement for preregistered gates',
  samples: [], passed: false }
const withComposition = process.env.ADAOS_E2E_PROCUREMENT_COMPOSITION === '1'
// Validate a one-item request before considering separate lines. An additional
// supplier-quote editor also links to a request; it is not a request-line editor.
const directComposition = directRequestCompositionFields(createForm, createAction, semantic)
if (withComposition) report.scope = 'post-hoc supplemental new request, supplier-offer line, quantity, reload and persisted local approval; no new LLM calls'
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: `e2e-procurement:${webspace}`, adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace: preview.webspace_id })
    const page = await context.newPage()
    page.setDefaultTimeout(15_000)
    const sample = { layout, checks: [], errors: [], mutations: [], browser_lifecycle: [], failed_requests: [], operation_responses: [] }
    report.samples.push(sample)
    await installSystemOverlayDismissal(page)
    page.on('pageerror', error => sample.errors.push(error.message))
    page.on('crash', () => sample.browser_lifecycle.push('page-crashed'))
    page.on('close', () => sample.browser_lifecycle.push('page-closed'))
    page.on('requestfailed', request => sample.failed_requests.push({ path: new URL(request.url()).pathname,
      failure: request.failure()?.errorText }))
    page.on('response', response => {
      if (new URL(response.url()).pathname === '/api/resources/operate') sample.operation_responses.push({ status: response.status() })
    })
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/resources/operate') sample.mutations.push(request.postDataJSON())
    })
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
    const revealTaskWidget = async target => {
      await revealPrototypeWidget(page, widgets, target)
    }
    const button = (form, action) => host(form.id).locator(`[data-command-id=${JSON.stringify(action.id)}]`).locator('button')
    const modalId = form => Object.entries(application.modals || {}).find(([, modal]) => modal.schema?.widgets?.some(widget => widget.id === form.id))?.[0]
    const opener = (form, create) => {
      const modal = modalId(form)
      for (const owner of widgets) {
        const isCreate = action => action.on === `click:${createCommand.id}` || action.on === 'click:new'
        const action = owner.actions?.find(action => action.on?.startsWith('click:')
          && (modal ? ['openModal', 'navigate'].includes(action.type) && action.params?.modalId === modal
            : create && action.type === 'updateState' && action.params?.[form.inputs.selectedStateKey] === '')
          && (create ? isCreate(action) : !isCreate(action)))
        if (action) return { owner, id: action.on.slice('click:'.length) }
      }
      return null
    }
    const openCreate = async () => {
      const control = opener(createForm, true)
      if (!control) throw new Error('No reachable create-request editor control')
      await revealTaskWidget(control.owner)
      await host(control.owner.id).locator(`[data-command-id=${JSON.stringify(control.id)}]`).click()
      if (!modalId(createForm)) await revealTaskWidget(createForm)
      await expect(host(createForm.id)).toBeVisible()
    }
    const fillCreate = async marker => {
      let markerField
      for (const field of createForm.inputs.fields || []) {
        if (field.readOnly || createAction.params?.payload?.[field.id] !== `$event.values.${field.id}`) continue
        const container = host(createForm.id).locator(`[data-webui-field-id=${JSON.stringify(field.id)}]`)
        if (!await container.isVisible()) continue
        const input = container.locator('input:not([type=hidden]),textarea,select').first()
        if (['shortText', 'longText'].includes(field.type)) {
          await input.fill(marker)
          markerField ||= field.id
        } else if (['number', 'integer'].includes(field.type)) await input.fill('3')
        else if (field.type === 'date') await input.fill('2026-10-09')
        else if (field.type === 'time') await input.fill('12:30')
        else if (['dropdown', 'singleChoice'].includes(field.type)) {
          const select = container.locator('select')
          if (await select.count()) {
            await expect.poll(() => select.locator('option').count(), { timeout: 15_000,
              message: `Public reference options must load for ${field.id}` }).toBeGreaterThan(1)
            await select.selectOption({ index: 1 })
          } else await container.locator('input[type=radio]').first().check()
        } else throw new Error(`Probe cannot qualify create field type ${field.type}`)
      }
      return markerField
    }
    const operate = async (form, action) => {
      const target = action.target
      if (!target.startsWith(`prototype.project.${scenario}.`)) throw new Error('Mutation outside owned test scenario')
      const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/resources/operate', { timeout: 15_000 })
      void pending.catch(() => {})
      await expect(button(form, action)).toBeEnabled()
      await button(form, action).click()
      if (action.confirmation) await page.locator('ion-alert').last().locator('button').last().click()
      const response = await pending
      const body = await response.json()
      if (!response.ok() || body.ok === false) throw new Error(`Operation rejected: ${JSON.stringify(body)}`)
      return body.result
    }
    try {
      if (!createAction || !transitionAction || !collection) throw new Error('Missing create/approval commands or request collection')
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync.materialization.currentScenario === expected
      }, scenario, { timeout: 60_000 })
      await dismissSystemOverlays(page)
      const comparison = withComposition ? (directComposition?.basic || directComposition?.manual?.comparison || supplierComparisonMetadata(semantic)) : null
      if (withComposition) {
        if (!comparison) throw new Error('No independently qualified public multi-supplier comparison')
        await qualifyBasicSupplierComparison({ page, context, widgets, collection, basic: comparison,
          hub, token, scenario, sample })
      }
      await openCreate()
      const marker = `PROC-${layout}-${Date.now()}`
      const markerField = await fillCreate(marker)
      let expectedComposition
      if (withComposition && directComposition) {
        const quantity = layout === 'wide' ? 7 : 9
        await host(createForm.id).locator(`[data-webui-field-id=${JSON.stringify(directComposition.quantity.id)}] input`).fill(String(quantity))
        expectedComposition = { [directComposition.quantity.id]: quantity }
        if (directComposition.offer) {
          const field = directComposition.offer
          if (field.optionsDataSource.resourceType !== `prototype.project.${scenario}.${comparison.resource.id}`) {
            throw new Error('Selected-offer request does not reference the publicly compared quote resource')
          }
          await selectPrototypeFormOption(host(createForm.id), field, comparison.quoteIds[0])
          expectedComposition[field.id] = comparison.quoteIds[0]
        }
        if (directComposition.basic) {
          const field = directComposition.item
          // Angular ngValue encodes DOM option values (e.g. "1: item-id").
          // Use independently persisted identity and the public choice helper;
          // never mistake the framework's DOM token for a resource record ID.
          const response = await context.request.post(`${hub}/api/resources/query`, {
            headers: { 'X-AdaOS-Token': token }, data: { resource_type: field.optionsDataSource.resourceType,
              limit: 1000 },
          })
          const body = await response.json()
          const selectedItem = body.items?.find(item => item.id === directComposition.basic.itemId)?.id
          if (!response.ok() || !selectedItem) throw new Error('Selected item is not independently persisted')
          await selectPrototypeFormOption(host(createForm.id), field, selectedItem)
          expectedComposition[field.id] = selectedItem
        }
        if (directComposition.manual) {
          const manual = directComposition.manual
          const resourceType = `prototype.project.${scenario}.${manual.resource.id}`
          const response = await context.request.post(`${hub}/api/resources/query`, {
            headers: { 'X-AdaOS-Token': token }, data: { resource_type: resourceType, limit: 1000 },
          })
          const body = await response.json()
          const quote = body.items?.find(record => record.id === manual.resource.records[0].id)
          if (!response.ok() || !quote) throw new Error('Cannot independently read the supplier quote used for manual composition')
          for (const key of ['item', 'supplier', 'price', 'currency', 'lead']) {
            const field = manual[key]
            // Delivery lead must be visible in the source quote, but need not
            // be duplicated as an editable field in a one-item request.
            if (!field) continue
            const value = quote[manual.quoteFields[key]]
            if (value == null || value === '') throw new Error(`Incomplete supplier quote: ${key}`)
            const container = host(createForm.id).locator(`[data-webui-field-id=${JSON.stringify(field.id)}]`)
            if (['number', 'integer', 'shortText', 'longText'].includes(field.type)) await container.locator('input,textarea').fill(String(value))
            else await selectPrototypeFormOption(host(createForm.id), field, value)
            expectedComposition[field.id] = value
          }
          sample.quote_provenance = { resourceType, quoteId: quote.id, representation: 'manual-copy-through-public-form' }
        }
      }
      const createdRecord = await operate(createForm, createAction)
      const recordId = createdRecord?.record_id || createdRecord?.record?.id
      if (!recordId || createdRecord?.record?.id !== recordId || (markerField && createdRecord.record[markerField] !== marker)) {
        throw new Error('Create returned inconsistent request identity')
      }
      sample.created_request = { recordId, marker, markerField }
      sample.checks.push({ task: 'create-request', status: 'passed', recordId })
      await page.reload({ waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === expected,
        scenario, { timeout: 60_000 })
      await revealTaskWidget(collection)
      const row = await prototypeRowById(host(collection.id), collection, recordId)
      await expect(row).toBeVisible()
      await selectPrototypeRow(row)
      sample.checks.push({ task: 'reload/select-created-request', status: 'passed', recordId })
      if (withComposition && directComposition) {
        const offerId = directComposition.offer ? createdRecord.record[directComposition.offer.id] : sample.quote_provenance?.quoteId
        const quantity = layout === 'wide' ? 7 : 9
        if ((!directComposition.basic && !offerId) || Object.entries(expectedComposition).some(([key, value]) => createdRecord.record[key] !== value)) {
          throw new Error('Created one-item request lacks the selected quote values or quantity')
        }
        if (directComposition.offer) expectedComposition[directComposition.offer.id] = offerId
        const response = await context.request.post(`${hub}/api/resources/query`, {
          headers: { 'X-AdaOS-Token': token }, data: { resource_type: createAction.target, filters: { id: recordId }, limit: 1000 },
        })
        const body = await response.json()
        const persisted = body.items?.find(item => item.id === recordId)
        if (!response.ok() || !persisted || Object.entries(expectedComposition).some(([key, value]) => persisted[key] !== value)) {
          throw new Error('Cannot independently prove persisted one-item request composition')
        }
        await expect(row).toContainText(String(quantity))
        sample.checks.push({ task: 'compose-request/select-supplier-offer/quantity/persisted-parent', status: 'passed',
          representation: directComposition.basic ? 'single-item-request/independent-supplier-comparison'
            : directComposition.manual ? 'single-item-request/manual-supplier-quote' : 'single-item-request',
          requestId: recordId, lineId: recordId, offerId, quantity, persistedFields: expectedComposition })
        sample.checks.push({ task: 'reload/select-new-request/visible-composition', status: 'passed', requestId: recordId, lineId: recordId })
      } else if (withComposition) {
        const { qualifyRequestComposition } = await import('./prototype-procurement-lines.mjs')
        await qualifyRequestComposition({ page, context, widgets, forms, createAction,
          requestCollection: collection, requestId: recordId, marker, operate, hub, token, sample, scenario, output, layout, semantic })
      }
      if (!modalId(transitionForm)) await revealTaskWidget(transitionForm)
      if (!await host(transitionForm.id).isVisible()) {
        const control = opener(transitionForm, false)
        if (!control) throw new Error('Approval command exists but no public control opens its editor for a selected request')
        const direct = host(control.owner.id).locator(`[data-command-id=${JSON.stringify(control.id)}]`)
        if (await direct.count() === 1) await direct.click()
        else await row.locator(`[data-command-id=${JSON.stringify(control.id)}]`).click()
      }
      await expect(host(transitionForm.id)).toBeVisible()
      await expect.poll(() => host(transitionForm.id).evaluate(element => window.ng?.getComponent(element.querySelector('ada-form-widget'))?.recordValues?.id),
        { timeout: 15_000, message: 'Refusing to transition until the editor loads the request created by this probe' }).toBe(recordId)
      await operate(transitionForm, transitionAction)
      if (sample.mutations.at(-1)?.record_id !== recordId) throw new Error('Approval targeted the wrong request')
      const response = await context.request.post(`${hub}/api/resources/query`, {
        headers: { 'X-AdaOS-Token': token }, data: { resource_type: transitionAction.target, filters: { id: recordId }, limit: 1000 },
      })
      const body = await response.json()
      const persisted = body.items?.find(item => item.id === recordId)
      if (!response.ok() || !persisted) throw new Error('Cannot independently read persisted approval state')
      const expectedTransition = transitionPersistenceExpectations(transitionAction, transition, createdRecord.record)
      for (const [key, value] of Object.entries(expectedTransition)) {
        if (persisted[key] !== value) throw new Error(`Approval state ${key} did not persist`)
      }
      sample.checks.push({ task: 'submit-for-approval/persisted-state', status: 'passed', recordId })
      await page.screenshot({ path: path.join(output, `${layout}-complete.png`), fullPage: true })
    } catch (error) {
      sample.failure = error.message
      sample.delivery_diagnostics = await page.evaluate(() => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return { ready: sync?.materializationReady ?? null, providerSynced: sync?.providerSynced ?? null,
          readinessState: sync?.materializationReadinessState ?? null,
          materialization: sync?.materialization ?? null }
      }).catch(() => null)
      sample.text = await page.locator('body').innerText({ timeout: 2000 }).catch(() => '')
      await page.screenshot({ path: path.join(output, `${layout}-failure.png`), fullPage: true, timeout: 5000 }).catch(() => {})
    } finally {
      await fs.writeFile(path.join(output, 'review.json'), JSON.stringify(report, null, 2) + '\n')
      await context.close()
    }
  }
} finally { await browser.close() }
report.passed = report.samples.length === 2 && report.samples.every(sample => !sample.failure && !sample.errors.length
  && sample.checks.some(check => check.task === 'submit-for-approval/persisted-state')
  && (!withComposition || sample.checks.some(check => check.task === 'reload/select-new-request/visible-composition')))
report.retention = 'Created records remain only in the owned, unapproved TEST prototype; no existing application data changed'
await fs.writeFile(path.join(output, 'review.json'), JSON.stringify(report, null, 2) + '\n')
console.log(JSON.stringify({ scenario, passed: report.passed, samples: report.samples.map(sample => ({ layout: sample.layout,
  checks: sample.checks, failure: sample.failure, errors: sample.errors })) }))
if (!report.passed) process.exitCode = 1
