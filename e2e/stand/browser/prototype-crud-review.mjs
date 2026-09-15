import { expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { revealPrototypeWidget } from './prototype-navigation.mjs'

// Opt-in qualification of separate scalar editors; never admitted to model context.
export async function reviewSeparateCrud({ page, application, source, sample, output, layout }) {
  const widgets = application.desktop.pageSchema.widgets
  const forms = Object.values(application.modals || {}).flatMap(modal => modal.schema?.widgets || [])
  const formFor = operation => forms.find(widget => widget.type === 'ui.form'
    && widget.actions?.some(action => action.type === 'resourceOperation' && action.params?.operation_id === operation))
  const createForm = formFor('create')
  const deleteForm = formFor('delete')
  if (!createForm || !deleteForm || createForm.id === deleteForm.id) throw new Error('Separate create/delete forms required')
  const actionFor = (form, operation) => form.actions.find(action => action.type === 'resourceOperation' && action.params?.operation_id === operation)
  const create = actionFor(createForm, 'create')
  const remove = actionFor(deleteForm, 'delete')
  const collection = widgets.find(widget => widget.type === 'ui.table' && widget.dataSource?.resourceType === create.target)
  if (!collection || remove.target !== create.target || !create.target.startsWith('prototype.')) throw new Error('Owned prototype CRUD resource required')
  const semantic = JSON.parse(await fs.readFile(path.join(source, 'semantic.webui.json'), 'utf8'))
  const resource = semantic.resources.find(resource => `prototype.${resource.id}` === create.target)
  const required = createForm.inputs.fields.filter(field => field.required && !field.readOnly)
  if (required.length !== 1 || required[0].type !== 'shortText') throw new Error('This probe requires one mandatory short-text field')
  if (createForm.inputs.fields.some(field => field.visibleIf || field.readOnly || !['shortText', 'longText', 'singleChoice'].includes(field.type))) {
    throw new Error('This probe does not qualify conditional, readonly or non-scalar create fields')
  }
  const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
  const fieldInput = (form, field) => host(form.id).locator(`[data-webui-field-id=${JSON.stringify(field.id)}] input, [data-webui-field-id=${JSON.stringify(field.id)}] textarea`).first()
  const button = (form, action) => host(form.id).locator(`[data-command-id=${JSON.stringify(action.id)}]`).locator('button')
  const rows = host(collection.id).locator('tr.row-selectable')
  const check = task => sample.checks.push({ status: 'passed', task, resource: create.target })
  const open = async form => {
    const modal = Object.entries(application.modals).find(([, value]) => value.schema.widgets.some(widget => widget.id === form.id))?.[0]
    const owner = widgets.find(widget => widget.actions?.some(action => action.type === 'openModal' && action.params?.modalId === modal))
    const action = owner?.actions.find(action => action.type === 'openModal' && action.params?.modalId === modal)
    if (!action) throw new Error(`No visible opener for ${form.id}`)
    const command = owner.type === 'ui.actions' ? action.on.replace(/^click:/, '') : action.id || action.on
    await host(owner.id).locator(`[data-command-id=${JSON.stringify(command)}]`).click()
    await expect(host(form.id)).toBeVisible()
  }
  const close = async () => {
    await page.locator('ion-modal').last().getByRole('button', { name: /Close|Закрыть/, exact: true }).click()
    await expect(page.locator('ion-modal.show-modal')).toHaveCount(0)
  }
  const operate = async (form, action) => {
    const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/resources/operate', { timeout: 15000 })
    void pending.catch(() => {})
    await button(form, action).click()
    if (action.confirmation) await page.locator('ion-alert').last().locator('button').last().click()
    const response = await pending
    const body = await response.json()
    if (!response.ok() || body.ok === false) throw new Error(`Operation rejected: ${JSON.stringify(body)}`)
    await expect(page.locator('ion-modal.show-modal')).toHaveCount(0)
    return body.result
  }

  const toolbar = widgets.find(widget => widget.type === 'ui.queryToolbar')
  await revealPrototypeWidget(page, widgets, collection)
  const search = toolbar?.inputs.controls.find(control => control.kind === 'search')
  const filter = toolbar?.inputs.controls.find(control => control.kind === 'filter' && control.inputType === 'select')
  if (!search || !filter) throw new Error('Search and choice filter required by this probe')
  const searchInput = host(toolbar.id).locator(`[data-query-id=${JSON.stringify(search.id)}] input`)
  const filterSelect = host(toolbar.id).locator(`[data-query-id=${JSON.stringify(filter.id)}] select`)
  if (!await filterSelect.isVisible()) await host(toolbar.id).getByRole('button', { name: /Filters|Фильтры/, exact: true }).click()
  await filterSelect.selectOption({ index: 0 })
  await searchInput.fill('')
  await expect(rows).toHaveCount(resource.records.length)
  for (const field of resource.fields.filter(field => field.value_type === 'short_text').slice(0, 2)) {
    const value = resource.records[0][field.id]
    if (!value) throw new Error(`Nonempty fixture required for search by ${field.id}`)
    await searchInput.fill(value)
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText(resource.records[0][required[0].id])
    check(`search/${field.id}`)
  }
  await searchInput.fill(`no-match-${Date.now()}`)
  await expect(rows).toHaveCount(0)
  await expect(host(collection.id)).toContainText(/пуст|empty/i)
  check('search/empty-result')
  await searchInput.fill('')
  const option = filter.options.find(option => option.value)
  await filterSelect.selectOption({ label: await filterSelect.locator('option').nth(1).innerText() })
  const filterField = resource.fields.find(field => field.value_type === 'choice')
  await expect(rows).toHaveCount(resource.records.filter(record => record[filterField.id] === option.value).length)
  check('choice-filter/change')
  await filterSelect.selectOption({ index: 0 })
  await expect(rows).toHaveCount(resource.records.length)

  await open(createForm)
  await expect(fieldInput(createForm, required[0])).toHaveValue('')
  const before = sample.mutations.length
  await button(createForm, create).click()
  await expect(host(createForm.id).locator('.field-error')).toBeVisible()
  if (sample.mutations.length !== before) throw new Error('Invalid form submitted a resource mutation')
  check('create/required-field-rejection/no-mutation')
  await page.screenshot({ path: path.join(output, `${layout}-required-field.png`), fullPage: true })
  const marker = `CRUD-${layout}-${Date.now()}`
  await fieldInput(createForm, required[0]).fill(marker)
  await close()
  if (sample.mutations.length !== before) throw new Error('Cancelled create submitted a resource mutation')
  await open(createForm)
  await expect(fieldInput(createForm, required[0])).toHaveValue('')
  check('create/cancel/no-mutation/clear-draft')
  await fieldInput(createForm, required[0]).fill(marker)
  const created = await operate(createForm, create)
  const createdId = created?.record_id
  if (!createdId || created.record?.id !== createdId) throw new Error('Create returned no consistent record identity')
  const createdRow = rows.filter({ hasText: marker })
  await expect(createdRow).toHaveCount(1)
  await createdRow.click()
  check('create/optional-fields-empty/select-created-record')
  await open(deleteForm)
  const deletingRecord = await host(deleteForm.id).evaluate(element => window.ng?.getComponent(element.querySelector('ada-form-widget'))?.recordValues)
  if (deletingRecord?.id !== createdId || deletingRecord?.[required[0].id] !== marker) throw new Error('Refusing to delete a record not created by this probe')
  if (remove.confirmation) {
    const count = sample.mutations.length
    await button(deleteForm, remove).click()
    await page.locator('ion-alert').last().locator('button').first().click()
    await expect(page.locator('ion-alert')).toHaveCount(0)
    if (sample.mutations.length !== count) throw new Error('Cancelled delete mutated data')
    check('delete/confirmation-cancel/no-mutation')
  }
  await operate(deleteForm, remove)
  if (sample.mutations.at(-1)?.record !== createdId) throw new Error('Delete targeted the wrong record')
  await expect(createdRow).toHaveCount(0)
  await expect(rows).toHaveCount(resource.records.length)
  check('delete/confirmation-accept/collection-refresh')
}
