import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'

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
    await context.addInitScript(({ hub, token, subnet, webspace, locale }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-interactions', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: locale })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace: preview.webspace_id, locale: checkpoint.context.locale })
    const page = await context.newPage()
    page.setDefaultTimeout(30_000)
    page.setDefaultNavigationTimeout(60_000)
    const sample = { layout, checks: [], errors: [], mutations: [] }
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(error.message))
    page.on('crash', () => sample.errors.push('Browser page crashed'))
    page.on('close', () => { if (!sample.completed) sample.errors.push('Browser page closed before completion') })
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/resources/operate') {
        const body = request.postDataJSON()
        sample.mutations.push({ resource: body.resource_type, operation: body.operation_id, record: body.record_id, payload: body.payload })
      }
    })
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
    const operationResponse = () => {
      const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/resources/operate', { timeout: 15_000 })
      // Keep a click/confirmation failure from leaving an unhandled timeout.
      void pending.catch(() => {})
      return pending
    }
    const editorOpener = (modalId, row) => {
      for (const owner of widgets) {
        const action = owner.actions?.find(item => item.type === 'openModal' && item.params?.modalId === modalId
          && item.on?.startsWith('click:') && item.on !== 'click:new')
        if (!action) continue
        const id = owner.type === 'ui.actions' ? action.on.slice('click:'.length) : action.id || action.on
        return host(owner.id).locator(`[data-command-id=${JSON.stringify(id)}]`)
      }
      return row
    }
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync.materialization.currentScenario === expected
      }, scenario, { timeout: 60_000 })
      for (const { widget, modalId } of forms) {
        const update = widget.actions?.find(action => action.type === 'resourceOperation' && action.params?.operation_id === 'update')
        const editableField = field => update?.params?.payload?.[field.id] === `$event.values.${field.id}` && !field.visibleIf && !field.readOnly
        const field = widget.inputs.fields?.find(field => ['shortText', 'longText'].includes(field.type) && editableField(field))
          || widget.inputs.fields?.find(field => field.type === 'date' && editableField(field))
        const collection = widgets.find(item => ['ui.table', 'ui.list'].includes(item.type) && item.dataSource?.resourceType === update?.target)
        if (!update || !field || !collection) {
          sample.checks.push({ editor: widget.id, status: 'not_exercised', reason: 'No supported text/date update and matching collection' })
          continue
        }
        const row = host(collection.id).locator('tr.row-selectable, .collection-focus-item').first()
        await expect(row).toBeVisible({ timeout: 30_000 })
        await row.click()
        const form = host(widget.id)
        const opener = editorOpener(modalId, row)
        if (modalId && opener !== row) await opener.click()
        const input = form.locator(`[id=${JSON.stringify(`form-${widget.id}-${field.id}`)}]`).locator('input, textarea').or(form.locator(`input[id=${JSON.stringify(`form-${widget.id}-${field.id}`)}], textarea[id=${JSON.stringify(`form-${widget.id}-${field.id}`)}]`)).first()
        await expect(input).toBeVisible({ timeout: 15_000 })
        await expect(input).toBeEditable({ timeout: 30_000 })
        const original = await input.inputValue()
        sample.conditionDebug = await form.evaluate(element => {
          const component = window.ng?.getComponent(element.querySelector('ada-form-widget'))
          return { record: component?.recordValues, values: component?.values,
            buttons: component?.formActionButtons?.map(button => ({ id: button.id, disabled: component.buttonDisabled(button), enabledIf: button.enabledIf })) }
        })
        for (const command of widget.actions.filter(action => action.type === 'resourceOperation' && action.confirmation)) {
          const confirmedButton = form.locator(`[data-command-id=${JSON.stringify(command.id)}]`).locator('button')
          if (await confirmedButton.isDisabled()) {
            sample.checks.push({ editor: widget.id, command: command.id, status: 'not_exercised', reason: 'Confirmation command disabled for selected fixture' })
            continue
          }
          const count = sample.mutations.length
          await confirmedButton.click()
          const alert = page.locator('ion-alert').last()
          await expect(alert).toBeVisible()
          await alert.locator('button').first().click()
          await expect(page.locator('ion-alert')).toHaveCount(0)
          await expect(input).toHaveValue(original)
          if (sample.mutations.length !== count) throw new Error('Cancelled confirmation caused a mutation')
          sample.checks.push({ editor: widget.id, command: command.id, status: 'passed', task: 'confirmation-cancel/no-mutation' })
        }
        if (modalId) {
          const count = sample.mutations.length
          await input.fill(field.type === 'date' ? '2099-12-29' : `cancelled-${layout}`)
          await opener.evaluate(element => { window.__E2E_FOCUS_ORIGIN__ = element })
          await page.locator('ion-modal').last().getByRole('button', { name: 'Close', exact: true }).click()
          await expect(form).toHaveCount(0)
          sample.dismissFocus = await opener.evaluate(element => ({
            sameElement: element === window.__E2E_FOCUS_ORIGIN__, originConnected: window.__E2E_FOCUS_ORIGIN__?.isConnected,
            activeTag: document.activeElement?.tagName, activeCommand: document.activeElement?.getAttribute('data-command-id'),
            activeShadowTag: document.activeElement?.shadowRoot?.activeElement?.tagName,
          }))
          await expect(opener).toBeFocused()
          await opener.click()
          await expect(input).toHaveValue(original)
          if (sample.mutations.length !== count) throw new Error('Dismissing an editor caused a mutation')
          sample.checks.push({ editor: widget.id, status: 'passed', task: 'dismiss-without-save/restore-focus/reopen', surface: 'overlay' })
        }
        const marker = field.type === 'date' ? '2099-12-30' : `review-${checkpoint.run_id}-${layout}`
        await input.fill(marker)
        let uploadProof
        for (const attachment of widget.inputs.fields.filter(item => item.fileStorage === 'prototype' && update.params.payload[item.id])) {
          const fileInput = form.locator(`[data-webui-field-id=${JSON.stringify(attachment.id)}] input[type=file]`)
          if (!await fileInput.isVisible()) continue
          const bytes = await fs.readFile(path.resolve('src/adaos/integrations/adaos-client/src/assets/prototype/sample-image.jpg'))
          const name = `review-${layout}.jpg`
          const pending = page.waitForResponse(response => response.request().method() === 'PUT' && new URL(response.url()).pathname.endsWith('/attachments'))
          void pending.catch(() => {})
          await fileInput.setInputFiles({ name, mimeType: 'image/jpeg', buffer: bytes })
          const uploaded = await pending
          const receipt = await uploaded.json()
          if (!uploaded.ok() || receipt.sha256 !== createHash('sha256').update(bytes).digest('hex')) throw new Error(`Attachment upload failed: ${JSON.stringify(receipt)}`)
          await expect.poll(() => form.evaluate((element, fieldId) => window.ng?.getComponent(element.querySelector('ada-form-widget'))?.values?.[fieldId], attachment.id))
            .toEqual(attachment.multiple ? [receipt.ref] : receipt.ref)
          uploadProof = { field: attachment.id, name, sha256: receipt.sha256, ref: receipt.ref, multiple: attachment.multiple, bytes }
          break
        }
        const button = form.locator(`[data-command-id=${JSON.stringify(update.id)}]`)
        await expect(button).toBeEnabled()
        if (modalId) {
          const surface = page.locator('ion-modal').last().locator('.modal-wrapper')
          await expect(surface).toHaveCSS('opacity', '1')
          sample.overlayBackground = await surface.evaluate(element => getComputedStyle(element).backgroundColor)
          sample.overlayStyles = await surface.evaluate(element => {
            const result = []
            for (let node = element; node; node = node.assignedSlot || node.parentElement || node.getRootNode()?.host) {
              const style = getComputedStyle(node)
              result.push({ tag: node.tagName, classes: node.className, opacity: style.opacity, background: style.backgroundColor, filter: style.filter })
            }
            return result
          })
          if (sample.overlayBackground === 'rgba(0, 0, 0, 0)' || sample.overlayBackground === 'transparent') throw new Error('Editor overlay has no opaque surface')
        }
        await page.screenshot({ path: path.join(output, `${layout}-${widget.id}-edit.png`), fullPage: true, animations: 'disabled' })
        sample.beforeSubmit = await form.evaluate((element, fieldId) => {
          const component = window.ng?.getComponent(element.querySelector('ada-form-widget'))
          return { field: fieldId, values: component?.values, input: element.querySelector('input,textarea')?.value,
            actions: component?.widget?.actions, selectedRecordId: component?.selectedRecordId }
        }, field.id)
        const responsePromise = operationResponse()
        await button.click()
        if (update.confirmation) await page.locator('ion-alert').last().locator('button').last().click()
        const response = await responsePromise
        const result = await response.json()
        if (!response.ok() || result.ok === false) throw new Error(`Update rejected: ${JSON.stringify(result)}`)
        const mutation = sample.mutations.at(-1)
        if (!mutation?.record || mutation.payload[field.id] !== marker) throw new Error('Wrong record or payload was submitted')
        try {
        if (modalId) {
          await expect(page.locator('ion-modal').filter({ has: form })).toHaveCount(0)
          await opener.click()
        } else await page.reload({ waitUntil: 'domcontentloaded' })
        // Re-open and verify the persisted value, not merely the edited DOM.
        if (!modalId) {
          await page.waitForFunction(expected => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === expected, scenario, { timeout: 60_000 })
          await row.click()
        }
        await expect(input).toHaveValue(marker, { timeout: 30_000 })
        sample.checks.push({ editor: widget.id, status: 'passed', task: 'select/edit/save/reopen', surface: modalId ? 'overlay' : 'inline' })
        if (uploadProof) {
          const download = page.waitForEvent('download')
          void download.catch(() => {})
          await form.getByRole('button', { name: uploadProof.name, exact: true }).click()
          const file = await download
          const bytes = await fs.readFile(await file.path())
          if (!bytes.equals(uploadProof.bytes)) throw new Error('Downloaded attachment differs from uploaded bytes')
          sample.checks.push({ editor: widget.id, status: 'passed', task: 'upload/save/reopen/download', field: uploadProof.field, sha256: uploadProof.sha256, size: bytes.length })
        }
        } finally {
        // Fixture restoration is not a claimed user workflow: an assignment
        // command need not expose unassignment of the originally empty field.
        const body = response.request().postDataJSON()
        if (body.resource_type !== update.target || !body.resource_type.startsWith('prototype.')) throw new Error('Unsafe fixture restoration')
        const headers = Object.fromEntries(Object.entries(await response.request().allHeaders())
          .filter(([key]) => !['content-length', 'host', 'origin'].includes(key)))
        const restored = await context.request.post(`${hub}/api/resources/operate`, { headers,
          data: { ...body, payload: Object.fromEntries(Object.keys(body.payload)
            .map(key => [key, sample.conditionDebug.record[key] ?? null])) },
        })
        if (!restored.ok() || (await restored.json()).ok === false) throw new Error('Could not restore fixture')
        sample.checks.push({ editor: widget.id, status: 'passed', task: 'fixture-restore', evidenceKind: 'stand_cleanup' })
        }
        if (modalId) await page.locator('ion-modal').last().getByRole('button', { name: 'Close', exact: true }).click()
        const create = widget.actions.find(action => action.type === 'resourceOperation' && action.params?.operation_id === 'create')
        const remove = widget.actions.find(action => action.type === 'resourceOperation' && action.params?.operation_id === 'delete')
        const supportedTypes = ['shortText', 'longText', 'number', 'integer', 'date', 'singleChoice', 'boolean', 'toggle']
        if (!create || !remove || widget.inputs.fields.some(item => !supportedTypes.includes(item.type) || item.visibleIf)) {
          sample.checks.push({ editor: widget.id, status: 'not_exercised', task: 'create/read/delete', reason: 'Requires same-editor create/delete and supported unconditional fields' })
          continue
        }
        if (modalId) await expect(page.locator('ion-modal').filter({ has: form })).toHaveCount(0)
        await host(`open-${widget.id}`).locator('[data-command-id="new"]').click()
        const createdMarker = field.type === 'date' ? '2099-12-31' : `created-${checkpoint.run_id}-${layout}`
        const originalValues = sample.conditionDebug.values
        for (const item of widget.inputs.fields) {
          const container = form.locator(`[data-webui-field-id=${JSON.stringify(item.id)}]`)
          const value = item.id === field.id ? createdMarker : originalValues[item.id]
          if (['boolean', 'toggle'].includes(item.type)) {
            await container.locator('input[type=checkbox]').setChecked(Boolean(value))
          } else if (item.type === 'singleChoice') {
            const index = (item.options || []).findIndex(option => option.value === value)
            if (index >= 0) await container.locator('input[type=radio]').nth(index).check()
          } else {
            await container.locator('input,textarea').fill(value == null ? '' : String(value))
          }
        }
        const creating = operationResponse()
        await form.locator(`[data-command-id=${JSON.stringify(create.id)}]`).locator('button').click()
        if (create.confirmation) await page.locator('ion-alert').last().locator('button').last().click()
        const createdResponse = await creating
        const createdResult = await createdResponse.json()
        if (!createdResponse.ok() || createdResult.ok === false) throw new Error(`Create rejected: ${JSON.stringify(createdResult)}`)
        if (sample.mutations.at(-1)?.payload?.[field.id] !== createdMarker) throw new Error('Create submitted the wrong draft')
        if (modalId) await expect(page.locator('ion-modal').filter({ has: form })).toHaveCount(0)
        // The new record must be discoverable through the collection before deletion.
        const newRow = host(collection.id).locator('tr.row-selectable, .collection-focus-item').filter({ hasText: createdMarker })
        await expect(newRow).toHaveCount(1, { timeout: 30_000 })
        await newRow.click()
        if (modalId && opener !== row) await opener.click()
        await expect(input).toHaveValue(createdMarker, { timeout: 30_000 })
        const selected = await form.evaluate(element => window.ng?.getComponent(element.querySelector('ada-form-widget'))?.recordValues)
        if (selected?.[field.id] !== createdMarker || !selected.id || selected.id === sample.conditionDebug.record.id) throw new Error('Refusing to delete a record not created by this probe')
        const deleting = operationResponse()
        await form.locator(`[data-command-id=${JSON.stringify(remove.id)}]`).locator('button').click()
        if (remove.confirmation) await page.locator('ion-alert').last().locator('button').last().click()
        const removedResponse = await deleting
        if (!removedResponse.ok() || (await removedResponse.json()).ok === false) throw new Error('Delete rejected')
        if (sample.mutations.at(-1)?.record !== selected.id) throw new Error('Delete targeted the wrong record')
        if (modalId) await expect(page.locator('ion-modal').filter({ has: form })).toHaveCount(0)
        await expect(newRow).toHaveCount(0, { timeout: 30_000 })
        sample.checks.push({ editor: widget.id, status: 'passed', task: 'create/read/delete', surface: modalId ? 'overlay' : 'inline' })
      }
      if (!sample.checks.some(check => check.status === 'passed')) throw new Error('No mutation exercised; this is not a task pass')
      await page.screenshot({ path: path.join(output, `${layout}-complete.png`), fullPage: true })
    } catch (error) {
      sample.failure = error.message
      sample.text = await page.locator('body').innerText({ timeout: 2000 }).catch(error => `Diagnostics unavailable: ${error.message}`)
      await page.screenshot({ path: path.join(output, `${layout}-failure.png`), fullPage: true, timeout: 5000 })
        .catch(error => { sample.diagnosticFailure = error.message })
    } finally {
      sample.completed = true
      await fs.writeFile(path.join(output, 'review.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
      await context.close()
    }
  }
} finally { await browser.close() }
report.passed = report.samples.length === 2 && report.samples.every(sample => !sample.failure && !sample.errors.length)
await fs.writeFile(path.join(output, 'review.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify(report))
if (!report.passed) process.exitCode = 1
