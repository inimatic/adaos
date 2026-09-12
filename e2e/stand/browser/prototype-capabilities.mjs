import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

if (process.env.ENV_TYPE !== 'dev') throw new Error('Requires ENV_TYPE=dev')
const checkpoint = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CHECKPOINT, 'utf8'))
const ownership = checkpoint.cleanup
const created = checkpoint.steps.find(step => step.id === 'create')?.output
const scenario = created?.scenario_id
if (!ownership?.test || ownership.status !== 'retained_for_review' || ownership.acceptance !== 'not_approved'
  || !ownership.owned_artifacts.some(item => item.primary_ref === `scenario:${scenario}` && item.project_id === scenario)) {
  throw new Error('Only owned, retained, unapproved test scenarios may be exercised')
}
const preview = ownership.previews.find(item => item.scenario_id === scenario && item.test && item.stage === 'prototype')
if (!preview) throw new Error('Missing owned preview')
const application = JSON.parse(await fs.readFile(path.join(created.artifact_root, 'webui.json'), 'utf8')).ui.application
const semantic = JSON.parse(await fs.readFile(path.join(created.artifact_root, 'semantic.webui.json'), 'utf8'))
const widgets = application.desktop.pageSchema.widgets
const navigation = widgets.find(widget => widget.inputs?.variant === 'tabs')
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output, { recursive: true })
const hub = process.env.ADAOS_E2E_HUB_URL
const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
if (!hub || !token || !subnet) throw new Error('Explicit hub, token and subnet required')
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: preview.webspace_id, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
const report = { scenario, samples: [], passed: false }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace, locale }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-capabilities', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: locale })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace: preview.webspace_id, locale: checkpoint.context.locale })
    const page = await context.newPage()
    page.setDefaultTimeout(20_000)
    const sample = { layout, checks: [], errors: [], operations: [], queries: [] }
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(error.message))
    const requests = new Map()
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/resources/query') requests.set(request, Date.now())
      if (new URL(request.url()).pathname === '/api/resources/operate') sample.operations.push(request.postDataJSON())
    })
    page.on('response', response => {
      const request = response.request()
      if (requests.has(request)) sample.queries.push({ elapsedMs: Date.now() - requests.get(request),
        endpoint: response.url(), status: response.status(), query: request.postDataJSON() })
    })
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
    const ready = async () => page.waitForFunction(expected => {
      const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
      return sync?.materializationReady && sync.materialization?.currentScenario === expected
    }, scenario, { timeout: 60_000 })
    const reveal = async widget => {
      if (await host(widget.id).isVisible()) return
      for (const button of navigation?.inputs?.buttons || []) {
        await host(navigation.id).locator(`[data-command-id=${JSON.stringify(button.id)}]`).click()
        if (await host(widget.id).isVisible()) return
      }
      throw new Error(`No reachable section for ${widget.id}`)
    }
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await ready()
      if (navigation) {
        for (const button of navigation.inputs.buttons) {
          await host(navigation.id).locator(`[data-command-id=${JSON.stringify(button.id)}]`).click()
          const expected = widgets.filter(widget => widget.visibleIf?.includes(`=== '${button.id}'`))
          if (!expected.length) throw new Error(`Tab ${button.id} has no owned content`)
          for (const widget of expected.filter(widget => !widget.visibleIf.startsWith('('))) await expect(host(widget.id)).toBeVisible()
          sample.checks.push({ kind: 'tab', id: button.id, owned: expected.map(widget => widget.id) })
        }
      }
      for (const widget of widgets) {
        const tableLinks = widget.type === 'ui.table'
          ? semantic.views.filter(view => view.selection_filter?.source_view_ref === widget.id) : []
        if (tableLinks.length) {
          await reveal(widget)
          const rows = host(widget.id).locator('tr.row-selectable')
          await expect(rows.first()).toBeVisible({ timeout: 20_000 })
          const available = await host(widget.id).locator('ada-table-widget').evaluate(element => window.ng.getComponent(element).pagedRows.map(row => row.id))
          for (const index of available.slice(0, 2).map((_, index) => index).reverse()) {
            await rows.nth(index).click()
            await expect(page.locator('ion-modal')).toHaveCount(0)
            for (const view of tableLinks) {
              const target = widgets.find(item => item.id === view.id)
              const expected = semantic.resources.find(resource => resource.id === view.resource_ref).records
                .filter(item => item[view.selection_filter.field_ref] === available[index]).map(item => item.id).sort()
              if (target.type !== 'ui.table') throw new Error(`Unexercised related table target: ${target.type}`)
              await expect.poll(() => host(target.id).locator('ada-table-widget').evaluate(element =>
                window.ng.getComponent(element).rows.map(row => row.id).sort()), { timeout: 20_000 }).toEqual(expected)
              sample.checks.push({ kind: 'table-related-selection', source: widget.id, target: target.id, selected: available[index], records: expected })
            }
          }
          const sourceView = semantic.views.find(view => view.id === widget.id)
          for (const editor of semantic.views.filter(view => view.resource_ref === sourceView.resource_ref && view.role === 'editor' && view.surface !== 'inline')) {
            const edit = host(`open-${editor.id}`).locator('[data-command-id="edit"]')
            if (!await edit.count()) continue
            await edit.click()
            await expect(host(editor.id)).toBeVisible()
            await page.locator('ion-modal').last().getByRole('button', { name: /Close|Закрыть/, exact: true }).click()
            sample.checks.push({ kind: 'explicit-related-editor', editor: editor.id })
          }
        }
        if (widget.type === 'ui.queryToolbar') {
          await reveal(widget)
          const toolbar = host(widget.id)
          const toggle = toolbar.locator('.query-toolbar__toggle')
          if (await toggle.count()) {
            await expect(toggle).toHaveAttribute('aria-expanded', 'false')
            await toggle.click()
            await expect(toggle).toHaveAttribute('aria-expanded', 'true')
          }
          for (const control of widget.inputs.controls.filter(item => item.inputType === 'date')) {
            const input = toolbar.locator(`[data-query-id=${JSON.stringify(control.id)}] input`)
            const collection = widgets.find(item => item.type === 'ui.table'
              && JSON.stringify(item.dataSource?.query || {}).includes(`$state.${control.stateKey}`))
            if (!collection) throw new Error('Date filter has no table consumer')
            const table = host(collection.id)
            await expect(table.locator('tr.row-selectable').first()).toBeVisible()
            const before = await table.locator('tbody tr').allTextContents()
            const response = page.waitForResponse(item => new URL(item.url()).pathname === '/api/resources/query'
              && item.request().postDataJSON()?.resource_type === collection.dataSource.resourceType
              && JSON.stringify(item.request().postDataJSON()).includes('2099-12-31'))
            void response.catch(() => {})
            // The native calendar commits change, without relying on input or blur.
            await input.evaluate(element => { element.value = '2099-12-31'; element.dispatchEvent(new Event('change', { bubbles: true })) })
            const body = await (await response).json()
            if (!body.ok || body.items?.length) throw new Error('Date commit did not filter the resource query')
            await expect(table.locator('tr.row-selectable')).toHaveCount(0)
            await toolbar.locator('.query-toolbar__reset').click()
            await expect.poll(() => table.locator('tbody tr').allTextContents()).toEqual(before)
            sample.checks.push({ kind: 'date-change-query-reset', widget: widget.id, before: before.length })
          }
          for (const control of widget.inputs.controls.filter(item => item.kind === 'search')) {
            const target = widgets.find(item => item.type === 'ui.list'
              && item.dataSource?.query?.search === `$state.${control.stateKey}`)
            const view = target && semantic.views.find(view => view.id === target.id && view.scope_filters?.length)
            if (!view) continue
            const expected = semantic.resources.find(resource => resource.id === view.resource_ref).records
              .filter(record => view.scope_filters.every(filter => record[filter.field_ref] === filter.value)).map(record => record.id).sort()
            const ids = () => host(target.id).locator('ada-list-widget').evaluate(element => window.ng.getComponent(element).latestItems.map(item => item.id).sort())
            await expect.poll(ids, { timeout: 20_000 }).toEqual(expected)
            await toolbar.locator(`[data-query-id=${JSON.stringify(control.id)}] input`).fill('e2e-no-matching-record-9fb3')
            await expect.poll(ids, { timeout: 20_000 }).toEqual([])
            await toolbar.locator('.query-toolbar__reset').click()
            await expect.poll(ids, { timeout: 20_000 }).toEqual(expected)
            sample.checks.push({ kind: 'scope-search-reset', widget: target.id, records: expected })
          }
          if (await toggle.count()) await toggle.click()
          sample.checks.push({ kind: 'query-disclosure', widget: widget.id, controls: widget.inputs.controls.length })
        }
        if (widget.type === 'collection.tree') {
          await reveal(widget)
          const nodes = host(widget.id).locator('.tree-widget__node')
          await expect(nodes.first()).toBeVisible()
          await nodes.first().click()
          await expect(nodes.first()).toHaveClass(/is-selected/)
          sample.checks.push({ kind: 'tree-selection', widget: widget.id, count: await nodes.count() })
          const linked = semantic.views.filter(view => view.selection_filter?.source_view_ref === widget.id)
          for (const view of linked) {
            const target = widgets.find(item => item.id === view.id)
            await reveal(target)
            const sourceResource = semantic.resources.find(resource => resource.id === semantic.views.find(item => item.id === widget.id).resource_ref)
            const field = view.selection_filter.field_ref
            const selection = widget.inputs.selectedStateKey
            if (target.dataSource.query.filters?.[field] !== `$state.${selection}`) throw new Error('Related query does not consume tree selection')
            for (const record of sourceResource.records.slice(0, 2).reverse()) {
              const node = nodes.filter({ has: page.locator('.tree-widget__node-title', { hasText: String(record[widget.inputs.titleKey]) }) })
              await node.click()
              await expect(node).toHaveClass(/is-selected/)
              const expected = semantic.resources.find(resource => resource.id === view.resource_ref).records
                .filter(item => item[field] === record.id).map(item => item.id).sort()
              if (target.type === 'ui.list') {
                await expect.poll(() => host(target.id).locator('ada-list-widget').evaluate(element =>
                  window.ng.getComponent(element).latestItems.map(item => item.id).sort()), { timeout: 20_000 }).toEqual(expected)
              } else throw new Error(`Linked presentation not yet exercised by this probe: ${target.type}`)
              sample.checks.push({ kind: 'tree-linked-query', source: widget.id, target: target.id, selected: record.id, records: expected })
            }
          }
        }
        if (widget.type === 'ui.list' && widget.inputs?.groupDisplay === 'accordion') {
          await reveal(widget)
          const group = host(widget.id).locator('ion-accordion').first()
          await expect(group).toBeVisible()
          const header = group.locator('[slot=header]')
          const before = await group.getAttribute('class')
          await header.click()
          await expect.poll(() => group.getAttribute('class')).not.toBe(before)
          await header.click()
          sample.checks.push({ kind: 'accordion-toggle', widget: widget.id })
        }
        if (widget.type === 'ui.list') {
          await reveal(widget)
          const view = semantic.views.find(view => view.id === widget.id)
          const records = semantic.resources.find(resource => resource.id === view.resource_ref).records
            .filter(record => (view.scope_filters || []).every(filter => record[filter.field_ref] === filter.value))
          if (records.length && !view.selection_filter && !view.filter) {
            await expect.poll(() => host(widget.id).locator('ada-list-widget').evaluate(element =>
              window.ng.getComponent(element).latestItems.length), { timeout: 20_000 }).toBeGreaterThan(0)
          }
          const text = await host(widget.id).locator('.note-card-meta-item, .list-row-meta-item').evaluateAll(elements => elements.map(element => {
            const style = getComputedStyle(element)
            return { value: element.textContent, overflow: element.classList.contains('text-truncate') ? 'truncate' : 'wrap',
              whiteSpace: style.whiteSpace, align: style.textAlign, width: element.clientWidth, scrollWidth: element.scrollWidth }
          }))
          if (text.some(item => item.overflow === 'wrap' && (item.whiteSpace === 'nowrap' || item.scrollWidth > item.width + 1))) {
            throw new Error('Wrapping metadata is clipped horizontally')
          }
          sample.checks.push({ kind: 'text-display', widget: widget.id, text })
          const scope = view.scope_filters?.find(filter => typeof filter.value === 'boolean')
          const editorEntry = scope && Object.entries(application.modals || {}).flatMap(([modalId, modal]) =>
            (modal.schema?.widgets || []).map(form => ({ modalId, form }))).find(({ form }) => form.type === 'ui.form'
              && form.inputs.fields.some(field => field.id === scope.field_ref && ['boolean', 'toggle'].includes(field.type))
              && form.actions.some(action => action.type === 'resourceOperation' && action.target === widget.dataSource.resourceType
                && action.params.operation_id === 'update' && action.params.payload?.[scope.field_ref]))
          if (editorEntry) {
            const { modalId, form } = editorEntry
            const row = host(widget.id).locator('.collection-focus-item').first()
            await expect(row).toBeVisible()
            await row.click()
            if (!widget.actions?.some(action => action.on === 'select' && action.type === 'openModal' && action.params?.modalId === modalId)) {
              await host(`open-${form.id}`).locator('[data-command-id="edit"]').click()
            }
            const formHost = host(form.id)
            await expect.poll(() => formHost.locator('ada-form-widget').evaluate(element => window.ng.getComponent(element).recordLoaded)).toBe(true)
            const original = await formHost.locator('ada-form-widget').evaluate(element => window.ng.getComponent(element).recordValues)
            await formHost.locator(`[data-webui-field-id=${JSON.stringify(scope.field_ref)}] input[type=checkbox]`).setChecked(!scope.value)
            const update = form.actions.find(action => action.type === 'resourceOperation' && action.params.operation_id === 'update')
            const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/resources/operate'
              && response.request().postDataJSON()?.record_id === original.id)
            void pending.catch(() => {})
            await formHost.locator(`[data-command-id=${JSON.stringify(update.id)}] button`).click()
            if (update.confirmation) await page.locator('ion-alert').last().locator('button').last().click()
            const response = await pending
            const receipt = await response.json()
            if (!response.ok() || !receipt.ok) throw new Error('Scoped record update failed')
            const body = response.request().postDataJSON()
            try {
              const visibleIds = () => host(widget.id).locator('ada-list-widget').evaluate(element => window.ng.getComponent(element).latestItems.map(item => item.id))
              await expect.poll(visibleIds).not.toContain(original.id)
              await page.reload({ waitUntil: 'domcontentloaded' })
              await ready()
              await reveal(widget)
              await expect.poll(() => host(widget.id).locator('ada-list-widget').evaluate(element => window.ng.getComponent(element).latestPayload != null)).toBe(true)
              await expect.poll(visibleIds).not.toContain(original.id)
              sample.checks.push({ kind: 'scope-mutation-reload', widget: widget.id, record: original.id, field: scope.field_ref })
            } finally {
              const headers = Object.fromEntries(Object.entries(await response.request().allHeaders())
                .filter(([key]) => !['content-length', 'host', 'origin'].includes(key)))
              const restored = await context.request.post(`${hub}/api/resources/operate`, { headers,
                data: { ...body, payload: Object.fromEntries(Object.keys(body.payload).map(key => [key, original[key] ?? null])) } })
              if (!restored.ok() || !(await restored.json()).ok) throw new Error('Scoped fixture restoration failed')
              sample.checks.push({ kind: 'fixture-restore', evidenceKind: 'stand_cleanup', record: original.id })
            }
            await page.reload({ waitUntil: 'domcontentloaded' })
            await ready()
            await reveal(widget)
            await expect.poll(() => host(widget.id).locator('ada-list-widget').evaluate(element =>
              window.ng.getComponent(element).latestItems.map(item => item.id)), { timeout: 20_000 }).toContain(original.id)
          }
        }
        if (widget.type === 'visual.metricChart') {
          await reveal(widget)
          const chart = host(widget.id)
          await expect(chart.locator('svg .dot').first()).toBeVisible()
          const points = await chart.locator('ada-metric-chart-widget').evaluate(element => window.ng.getComponent(element).series.points)
          if (!points.length || points.some(point => !Number.isFinite(point.y))) throw new Error('Invalid chart points')
          await expect(chart.locator('.point-y')).toHaveCount(points.length)
          sample.checks.push({ kind: 'numeric-chart', widget: widget.id, points })
        }
        if (widget.type === 'collection.board' && widget.inputs.dragDrop) {
          await reveal(widget)
          const board = host(widget.id)
          const card = board.locator('[data-webui-board-item-id]').first()
          await expect(card).toBeVisible()
          const record = await card.getAttribute('data-webui-board-item-id')
          const originalLane = await card.evaluate(element => element.closest('[data-webui-board-lane-id]').getAttribute('data-webui-board-lane-id'))
          const targetLane = widget.inputs.lanes.find(lane => lane.id !== originalLane).id
          const target = board.locator(`[data-webui-board-lane-id=${JSON.stringify(targetLane)}] .board-lane__items`)
          const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/resources/operate')
          void pending.catch(() => {})
          if (layout === 'compact') {
            // Keyboard/touch alternative is an actual client control, not a handler shortcut.
            await card.locator('.board-card__move select').selectOption(targetLane)
          } else {
            await card.scrollIntoViewIfNeeded()
            const from = await card.locator('.board-card__drag-handle').boundingBox()
            const to = await target.boundingBox()
            if (!from || !to) throw new Error('Unframed drag source or destination')
            await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2)
            await page.mouse.down()
            await page.mouse.move(from.x + from.width / 2 + 8, from.y + from.height / 2, { steps: 4 })
            await page.mouse.move(to.x + to.width / 2, to.y + Math.min(35, to.height / 2), { steps: 30 })
            await page.mouse.up()
          }
          const response = await pending
          if (!response.ok() || !(await response.json()).ok) throw new Error('Board move failed to persist')
          await page.reload({ waitUntil: 'domcontentloaded' })
          await ready()
          await reveal(widget)
          await expect(board.locator(`[data-webui-board-lane-id=${JSON.stringify(targetLane)}] [data-webui-board-item-id=${JSON.stringify(record)}]`)).toBeVisible()
          sample.checks.push({ kind: layout === 'compact' ? 'move-menu-persist-reload' : 'drag-persist-reload', widget: widget.id, record, originalLane, targetLane })
        }
      }
      const settings = widgets.find(widget => widget.id === 'prototype-settings')
      if (settings) {
        for (const button of settings.inputs.buttons) {
          const openSettings = () => host(settings.id).locator(`[data-command-id=${JSON.stringify(button.id)}]`).click()
          await openSettings()
          await expect(page.locator('ion-modal').last()).toBeVisible()
          await expect(page.locator('ion-modal').last().locator('ada-page-widget-host').first()).toBeVisible()
          await page.screenshot({ path: path.join(output, `${layout}-settings.png`), fullPage: true })
          const sectionId = settings.actions.find(action => action.on === `click:${button.id}`).params.modalId
          const members = application.modals[sectionId].schema.widgets
          const collection = members.find(widget => ['ui.table', 'ui.list'].includes(widget.type))
          const editor = Object.values(application.modals).flatMap(modal => modal.schema.widgets)
            .find(widget => widget.type === 'ui.form' && widget.actions?.some(action => action.type === 'resourceOperation'
              && action.params.operation_id === 'update' && action.target === collection?.dataSource?.resourceType))
          if (!collection || !editor) throw new Error('Settings have no collection and editable local record')
          const update = editor.actions.find(action => action.type === 'resourceOperation' && action.params.operation_id === 'update')
          const field = editor.inputs.fields.find(field => ['longText', 'shortText'].includes(field.type)
            && !field.readOnly && !field.visibleIf && update.params.payload?.[field.id] === `$event.values.${field.id}`)
          if (!field) throw new Error('No editable settings text field')
          const openRecord = async () => {
            await host(collection.id).locator('tr.row-selectable,.collection-focus-item').first().click()
            const opensEditor = collection.actions?.some(action => action.on === 'select' && action.type === 'openModal')
            if (!opensEditor) {
              const opener = members.find(widget => widget.type === 'ui.actions' && widget.actions.some(action => action.type === 'openModal' && action.on === 'click:edit'))
              if (!opener) throw new Error('Settings edit has no reachable opener')
              await host(opener.id).locator('[data-command-id="edit"]').click()
            }
            await expect(host(editor.id)).toBeVisible()
            await expect.poll(() => host(editor.id).locator('ada-form-widget').evaluate(element => window.ng.getComponent(element).recordLoaded)).toBe(true)
          }
          const input = () => host(editor.id).locator(`[data-webui-field-id=${JSON.stringify(field.id)}]`).locator('input,textarea').first()
          const save = async value => {
            await input().fill(value)
            const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/resources/operate'
              && response.request().postDataJSON()?.resource_type === update.target)
            void pending.catch(() => {})
            await host(editor.id).locator(`[data-command-id=${JSON.stringify(update.id)}] button`).click()
            const result = await pending
            if (!result.ok() || !(await result.json()).ok) throw new Error('Settings mutation failed')
            await expect(host(editor.id)).toHaveCount(0)
          }
          await openRecord()
          const original = await input().inputValue()
          const marker = `review-${layout}@example.invalid`
          await save(marker)
          await page.locator('ion-modal').last().getByRole('button', { name: /Close|Закрыть/, exact: true }).click()
          await page.reload({ waitUntil: 'domcontentloaded' })
          await ready()
          await openSettings()
          await openRecord()
          await expect(input()).toHaveValue(marker)
          await save(original)
          await page.locator('ion-modal').last().getByRole('button', { name: /Close|Закрыть/, exact: true }).click()
          sample.checks.push({ kind: 'settings-edit-persist-reload-restore', section: button.id, field: field.id })
        }
      }
      if (!sample.checks.length) throw new Error('No supported capability exercised')
    } catch (error) { sample.failure = error.message }
    sample.geometry = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }))
    await page.screenshot({ path: path.join(output, `${layout}.png`), fullPage: true })
    await fs.writeFile(path.join(output, `${layout}.txt`), await page.locator('body').innerText(), 'utf8')
    sample.passed = !sample.failure && !sample.errors.length && sample.geometry.document <= sample.geometry.viewport + 1
    await context.close()
  }
  report.passed = report.samples.every(sample => sample.passed)
} finally {
  await fs.writeFile(path.join(output, 'capabilities.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.passed) process.exitCode = 1
