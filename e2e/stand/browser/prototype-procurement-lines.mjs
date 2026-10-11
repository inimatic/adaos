import { expect } from '@playwright/test'
import path from 'node:path'
import { prototypeRowById, prototypeRowSelector, revealPrototypeWidget, selectPrototypeRow } from './prototype-navigation.mjs'
import { directRequestCompositionFields } from './prototype-procurement-fields.mjs'

// Supplemental, post-generation task coverage. Only public controls mutate state;
// an independent provider read proves that the line belongs to the new request.
export async function qualifyRequestComposition({ page, context, widgets, forms, createAction,
  requestCollection, requestId, marker, operate, hub, token, sample, scenario, output, layout, semantic }) {
  const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
  const editor = forms.find(form => form.actions?.some(action => action.type === 'resourceOperation'
    && action.params?.operation_id === 'create' && action.target !== createAction.target)
    && form.inputs.fields?.some(field => field.optionsDataSource?.resourceType === createAction.target))
  const createLine = editor?.actions?.find(action => action.type === 'resourceOperation' && action.params?.operation_id === 'create')
  const collection = widgets.find(widget => ['ui.table', 'ui.list', 'collection.board'].includes(widget.type)
    && widget.dataSource?.resourceType === createLine?.target)
  if (!editor || !createLine || !collection) throw new Error('No publicly composable request-line editor and collection')
  const parent = editor.inputs.fields.find(field => field.optionsDataSource?.resourceType === createAction.target)
  const offer = editor.inputs.fields.find(field => field.optionsDataSource && field !== parent)
  const quantity = editor.inputs.fields.find(field => ['number', 'integer'].includes(field.type)
    && createLine.params.payload?.[field.id] === `$event.values.${field.id}`)
  if (!parent || !offer || !quantity) throw new Error('Cannot identify request, supplier offer and quantity controls')
  // A linked item is not itself a supplier quote. The task requires comparing
  // quotes, not necessarily persisting an offer in a request line; distinguish
  // this optional stronger proof without mislabelling an ordinary item lookup.
  const quoteSelection = !!semantic && directRequestCompositionFields(editor, createLine, semantic)?.offer?.id === offer.id
  await revealPrototypeWidget(page, widgets, collection)
  const header = (collection.inputs.headerActions || []).find(button => button.id === createLine.id)
  if (!header) throw new Error('No public add-line header command')
  await host(collection.id).locator(`[data-command-id=${JSON.stringify(header.id)}]`).click()
  if (widgets.some(widget => widget.id === editor.id)) await revealPrototypeWidget(page, widgets, editor)
  await expect(host(editor.id)).toBeVisible()
  const options = field => host(editor.id).evaluate((element, fieldId) => {
    const component = window.ng?.getComponent(element.querySelector('ada-form-widget'))
    const control = component?.fields?.find(item => item.id === fieldId)
    return control ? component.choiceOptions(control) : []
  }, field.id)
  const choose = async (field, value) => {
    await expect.poll(async () => (await options(field)).some(option => option.value === value)).toBe(true)
    const index = (await options(field)).findIndex(option => option.value === value)
    const control = host(editor.id).locator(`[data-webui-field-id=${JSON.stringify(field.id)}]`)
    if (field.type === 'dropdown') await control.locator('select').selectOption({ index: index + 1 })
    else if (field.type === 'singleChoice') await control.locator('input[type=radio]').nth(index).check()
    else throw new Error(`Unsupported relationship control ${field.type}`)
  }
  await choose(parent, requestId)
  await expect.poll(async () => (await options(offer)).length).toBeGreaterThan(0)
  const selectedOffer = (await options(offer))[0]
  await choose(offer, selectedOffer.value)
  const amount = layout === 'wide' ? 7 : 9
  await host(editor.id).locator(`[data-webui-field-id=${JSON.stringify(quantity.id)}] input`).fill(String(amount))
  const guard = semantic?.commands?.find(command => command.id === createLine.id)?.guard
  const values = await host(editor.id).evaluate(element => {
    const component = window.ng?.getComponent(element.querySelector('ada-form-widget'))
    return { ...component?.state?.getSnapshot(), ...component?.recordValues, ...component?.values }
  })
  const requiredFieldIds = activeGuardRequiredFields(guard, values)
  sample.guard_requirements = { command: createLine.id, requiredFieldIds }
  await fillAdditionalRequiredLineFields(host(editor.id), editor, createLine,
    { skipFieldIds: [parent.id, offer.id, quantity.id], requiredFieldIds, marker })
  const created = await operate(editor, createLine)
  const lineId = created?.record_id || created?.record?.id
  if (!lineId || created.record?.[parent.id] !== requestId || created.record?.[offer.id] !== selectedOffer.value
    || created.record?.[quantity.id] !== amount) throw new Error('Create line returned incorrect ownership, offer or quantity')
  const response = await context.request.post(`${hub}/api/resources/query`, {
    headers: { 'X-AdaOS-Token': token }, data: { resource_type: createLine.target, filters: { id: lineId }, limit: 1000 },
  })
  const body = await response.json()
  const persisted = body.items?.find(item => item.id === lineId)
  if (!response.ok() || persisted?.[parent.id] !== requestId || persisted?.[offer.id] !== selectedOffer.value
    || persisted?.[quantity.id] !== amount) throw new Error('Cannot independently prove the persisted request composition')
  sample.checks.push({ task: quoteSelection ? 'compose-request/select-supplier-offer/quantity/persisted-parent'
    : 'compose-request/select-linked-record/quantity/persisted-parent', status: 'passed',
    requestId, lineId, selectedRecordId: selectedOffer.value, offerId: quoteSelection ? selectedOffer.value : null,
    quantity: amount, quote_reference_proven: quoteSelection })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForFunction(expected => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === expected,
    scenario, { timeout: 60_000 })
  await revealPrototypeWidget(page, widgets, requestCollection)
  const requestRow = await prototypeRowById(host(requestCollection.id), requestCollection, requestId)
  await expect(requestRow).toBeVisible()
  await selectPrototypeRow(requestRow)
  await revealPrototypeWidget(page, widgets, collection)
  const lines = host(collection.id).locator(prototypeRowSelector(collection))
  await expect(lines).toHaveCount(1)
  await expect(lines.first()).toContainText(String(amount))
  sample.checks.push({ task: 'reload/select-new-request/visible-composition', status: 'passed', requestId, lineId })
  await page.screenshot({ path: path.join(output, `${layout}-composition.png`), fullPage: true })
  // Supporting-panel layouts may hide the main request list; return through its
  // public disclosure before the original approval probe continues.
  const panelClose = page.locator('ada-layout-region.is-compact.is-open .layout-region__header button[aria-label="Close panel"]:visible')
  if (await panelClose.count()) await panelClose.last().click()
  await revealPrototypeWidget(page, widgets, requestCollection)
  await selectPrototypeRow(requestRow)
}

// A valid line may have additional required inputs (for example a unit choice).
// Fill them through visible public controls; do not weaken validation or change
// the independently asserted request/offer/quantity values.
export function activeGuardRequiredFields(guard, values) {
  if (!guard) return []
  const when = guard.when
  if (!when) throw new Error('Conditional guard predicate required')
  const value = values[when.field_ref]
  const nonempty = value !== null && value !== undefined && (typeof value === 'number' || typeof value === 'boolean' || value.length > 0)
  const active = when.operator === 'nonempty' ? nonempty : when.operator === 'empty' ? !nonempty
    : when.operator === 'equals' ? value === when.value : when.operator === 'not_equals' ? value !== when.value : undefined
  if (active === undefined) throw new Error(`Unsupported guard operator ${when.operator}`)
  return active ? guard.require_nonempty || [] : []
}

export async function fillAdditionalRequiredLineFields(formHost, editor, action, { skipFieldIds = [], requiredFieldIds = [], marker = 'E2E line' } = {}) {
  for (const field of editor.inputs.fields || []) {
    if ((!field.required && !requiredFieldIds.includes(field.id)) || field.readOnly || skipFieldIds.includes(field.id)
      || action.params?.payload?.[field.id] !== `$event.values.${field.id}`) continue
    const container = formHost.locator(`[data-webui-field-id=${JSON.stringify(field.id)}]`)
    if (!await container.isVisible()) continue
    if (['dropdown', 'singleChoice'].includes(field.type)) {
      const select = container.locator('select')
      if (await select.count()) {
        if (await select.evaluate(element => element.selectedIndex > 0)) continue
        await expect.poll(() => select.locator('option').count()).toBeGreaterThan(1)
        await select.selectOption({ index: 1 })
      } else if (!await container.locator('input[type=radio]:checked').count()) {
        await container.locator('input[type=radio]').first().check()
      }
    } else if (field.type === 'boolean') {
      await container.locator('input[type=checkbox]').check()
    } else {
      const input = container.locator('input:not([type=hidden]),textarea').first()
      if (await input.inputValue()) continue
      if (['shortText', 'longText'].includes(field.type)) await input.fill(marker)
      else if (['number', 'integer'].includes(field.type)) await input.fill('3')
      else if (field.type === 'date') await input.fill('2026-10-09')
      else if (field.type === 'time') await input.fill('12:30')
      else throw new Error(`Probe cannot fill required line field type ${field.type}`)
    }
  }
}
