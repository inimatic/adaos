// Independent acceptance fixture. Never supplied as model generation context.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const env = process.env
if (env.ENV_TYPE !== 'dev' || env.ADAOS_E2E_REVIEW_STAGE !== 'automation') throw new Error('DEV Automation only')
const pin = JSON.parse(await fs.readFile(env.ADAOS_E2E_SNAPSHOT_PIN, 'utf8'))
const application = JSON.parse(await fs.readFile(env.ADAOS_E2E_EXPECTED_WEBUI, 'utf8')).ui.application
const forms = Object.values(application.modals).flatMap(modal => modal.schema.widgets).filter(widget => widget.type === 'ui.form')
const scenario = env.ADAOS_E2E_SCENARIO_ID
const webspace = env.ADAOS_E2E_WEBSPACE_ID
const subnet = env.ADAOS_E2E_SUBNET_ID
const hub = env.ADAOS_E2E_HUB_URL
const token = env.ADAOS_E2E_HUB_TOKEN
if (pin.scenario_id !== scenario || pin.stage !== 'automation' || !scenario.startsWith('test_')
  || !webspace.startsWith('preview-') || !token) throw new Error('An explicitly pinned test Automation is required')
const output = path.resolve(env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output, { recursive: true })
const url = new URL(env.ADAOS_E2E_CLIENT_URL)
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: webspace, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
const report = { scope: 'DEV owner browser journey; not delegated authorization acceptance', scenario,
  task: pin.revision, records: 'New TEST journey records retained for inspection; existing records untouched', samples: [] }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const sample = { layout, checks: [], errors: [], commands: [], network: [] }
    report.samples.push(sample)
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      window.__E2E_ALERT_DISMISS__ = []
      window.addEventListener('ionAlertDidDismiss', event => window.__E2E_ALERT_DISMISS__.push(event.detail))
      for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-equipment-journey', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru',
        'adaos.runtime_debug': '1', 'adaos.runtime_debug.opt_in.v1': '1' })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace })
    const page = await context.newPage()
    page.setDefaultTimeout(15_000)
    page.on('pageerror', error => sample.errors.push(error.message))
    const responseReads = []
    const pendingReads = new Set()
    const startedAt = new WeakMap()
    page.on('request', request => {
      if (new URL(request.url()).pathname !== '/api/tools/call') return
      startedAt.set(request, Date.now())
      pendingReads.add(request)
    })
    page.on('requestfailed', request => pendingReads.delete(request))
    page.on('response', response => {
      if (new URL(response.url()).pathname !== '/api/tools/call') return
      pendingReads.delete(response.request())
      responseReads.push(response.json().then(body => sample.network.push({
        tool: response.request().postDataJSON()?.tool, args: response.request().postDataJSON()?.arguments,
        elapsedMs: Date.now() - startedAt.get(response.request()),
        at: new Date().toISOString(), status: response.status(),
        ok: body.ok, error: body.error, detail: body.detail, result: body.result,
      })).catch(() => {}))
    })
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
    const command = (widget, id = widget) => host(widget).locator(`[data-command-id=${JSON.stringify(id)}]`)
    const field = (form, id) => host(form).locator(`[data-webui-field-id=${JSON.stringify(id)}]`)
    const fill = (form, id, text) => field(form, id).locator('input,textarea').fill(text)
    const rows = widget => host(widget).locator('tr.row-selectable,.collection-focus-item')
    const checkpoint = async (id, work) => {
      sample.active = id
      await work()
      sample.checks.push({ id, status: 'passed' })
      sample.active = null
      console.log(`${layout}: ${id} passed`)
    }
    const submit = async (form, button, ok = true) => {
      const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/tools/call'
        && response.request().postDataJSON()?.tool?.includes('save_record'))
      void pending.catch(() => {})
      await command(form, button).click()
      const confirmation = forms.find(widget => widget.id === form)?.actions.find(action => action.id === button)?.confirmation
      if (confirmation) {
        await page.locator('ion-alert').last().getByRole('button', { name: confirmation.confirmLabel, exact: true }).click()
        await expect(page.locator('ion-alert')).toHaveCount(0)
      }
      const response = await pending
      const body = await response.json()
      const result = body.result ?? body
      sample.commands.push({ status: response.status(), tool: response.request().postDataJSON().tool, result })
      expect(response.ok() && body.ok !== false && result.ok !== false).toBe(ok)
      if (ok) await expect(host(form)).toHaveCount(0)
      else await expect(host(form)).toBeVisible()
      return result
    }
    const selectParent = async (form, id, caption) => {
      const select = field(form, id).locator('select')
      await expect(select).toBeEnabled()
      await expect(select.locator('option', { hasText: caption })).toHaveCount(1)
      await select.selectOption({ label: (await select.locator('option', { hasText: caption }).textContent()).trim() })
      await expect(select).not.toHaveValue('')
    }
    const marker = `E2E-${layout}-${Date.now()}`
    const ready = async () => {
      const started = Date.now()
      await page.waitForFunction(id => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.providerSynced && sync?.materializationReady && sync?.materialization?.currentScenario === id
      }, scenario, { timeout: 60_000 })
      ;(sample.readinessMs ??= []).push(Date.now() - started)
    }
    const dismiss = () => page.locator('ion-modal').last().getByRole('button', { name: /Close|Закрыть/, exact: true }).click()
    const openInspection = () => command('open-inspection_editor', 'edit').click()
    sample.marker = marker
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await ready()
      await checkpoint('create-equipment', async () => {
        await command('create-asset').click()
        await fill('asset_editor', 'name', marker)
        await fill('asset_editor', 'location', 'E2E location')
        await submit('asset_editor', 'save-asset')
        await expect(rows('assets_col').filter({ hasText: marker })).toHaveCount(1)
      })
      await checkpoint('select-and-edit-equipment', async () => {
        await rows('assets_col').filter({ hasText: marker }).click()
        await expect(host('asset_editor')).toHaveCount(0)
        await command('edit-asset').click()
        await expect(field('asset_editor', 'name').locator('input')).toHaveValue(marker)
        await fill('asset_editor', 'location', 'Updated location')
        await submit('asset_editor', 'save-asset')
        await expect(rows('assets_col').filter({ hasText: marker })).toContainText('Updated location')
      })
      await checkpoint('cancel-edit-equipment', async () => {
        await command('edit-asset').click()
        await fill('asset_editor', 'location', 'Must not be saved')
        await dismiss()
        await command('edit-asset').click()
        await expect(field('asset_editor', 'location').locator('input')).toHaveValue('Updated location')
        await dismiss()
      })
      await checkpoint('injected-write-error-retains-draft', async () => {
        await command('edit-asset').click()
        await fill('asset_editor', 'location', 'Retained draft')
        const failWrite = route => route.request().postDataJSON()?.tool?.endsWith(':save_record')
          ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true,
            result: { ok: false, error: 'storage_unavailable', message: 'E2E injected storage failure' } }) })
          : route.continue()
        await page.route('**/api/tools/call', failWrite)
        try {
          await submit('asset_editor', 'save-asset', false)
          await expect(field('asset_editor', 'location').locator('input')).toHaveValue('Retained draft')
        } finally { await page.unroute('**/api/tools/call', failWrite) }
        await fill('asset_editor', 'location', 'Updated location')
        await submit('asset_editor', 'save-asset')
      })
      await checkpoint('create-inspection-visible-parent', async () => {
        await command('create-inspection').click()
        await selectParent('inspection_editor', 'asset_id', marker)
        await fill('inspection_editor', 'started_at', '2026-09-13')
        await fill('inspection_editor', 'note', marker)
        await page.screenshot({ path: path.join(output, `${layout}-parent.png`), fullPage: true })
        await submit('inspection_editor', 'cmd_save_draft')
        await expect(rows('inspections_col').filter({ hasText: marker })).toHaveCount(1)
      })
      await checkpoint('reject-completion-without-items', async () => {
        await rows('inspections_col').filter({ hasText: marker }).click()
        await command('open-inspection_editor', 'edit').click()
        await submit('inspection_editor', 'cmd_complete_inspection', false)
        await expect(field('inspection_editor', 'note').locator('textarea')).toHaveValue(marker)
        await dismiss()
      })
      await checkpoint('create-defect-draft-without-comment', async () => {
        await command('create-item').click()
        await selectParent('item_editor', 'inspection_id', marker)
        await fill('item_editor', 'title', marker + '-item')
        const declared = forms.find(form => form.id === 'item_editor').inputs.fields.find(field => field.id === 'result')
        const index = declared.options.findIndex(option => option.value === 'defect')
        await field('item_editor', 'result').locator('input[type=radio]').nth(index).check()
        await submit('item_editor', 'cmd_save_item')
        await expect(rows('items_col').filter({ hasText: marker })).toHaveCount(1)
      })
      await checkpoint('reject-defect-completion', async () => {
        await openInspection()
        const result = await submit('inspection_editor', 'cmd_complete_inspection', false)
        expect(result.error).toBe('incomplete_inspection')
        await expect(field('inspection_editor', 'note').locator('textarea')).toHaveValue(marker)
        await dismiss()
      })
      await checkpoint('invalid-link-retains-draft-then-fix', async () => {
        await rows('items_col').filter({ hasText: marker }).click()
        await fill('item_editor', 'comment', 'Observed defect')
        await fill('item_editor', 'defect_photo', 'http://example.org/photo.jpg')
        const result = await submit('item_editor', 'cmd_save_item', false)
        expect(result.error).toBe('invalid_photo_link')
        await expect(field('item_editor', 'comment').locator('textarea')).toHaveValue('Observed defect')
        await fill('item_editor', 'defect_photo', 'https://example.org/photo.jpg')
        await submit('item_editor', 'cmd_save_item')
      })
      await checkpoint('complete-and-lock', async () => {
        await openInspection()
        const result = await submit('inspection_editor', 'cmd_complete_inspection')
        expect(result.item.status).toBe('completed')
        await openInspection()
        await expect(field('inspection_editor', 'note').locator('textarea')).toBeDisabled()
        await dismiss()
        await rows('items_col').filter({ hasText: marker }).click()
        await expect(field('item_editor', 'comment').locator('textarea')).toBeDisabled()
        await dismiss()
      })
      await checkpoint('reload-and-reopen', async () => {
        await page.reload({ waitUntil: 'domcontentloaded' })
        await ready()
        await rows('assets_col').filter({ hasText: marker }).click()
        await rows('inspections_col').filter({ hasText: marker }).click()
        await expect(rows('items_col').filter({ hasText: marker })).toContainText('Observed defect')
        await rows('items_col').filter({ hasText: marker }).click()
        await expect(field('item_editor', 'defect_photo').locator('input')).toHaveValue('https://example.org/photo.jpg')
        await expect(field('item_editor', 'comment').locator('textarea')).toBeDisabled()
        await dismiss()
      })
      await checkpoint('query-filters-and-search', async () => {
        const filter = async (widget, value) => {
          const declaration = application.desktop.pageSchema.widgets.find(item => item.id === widget)
          const control = declaration.inputs.controls.find(item => item.kind === 'filter')
          const toggle = host(widget).locator('.query-toolbar__toggle')
          if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click()
          await host(widget).locator('select').selectOption({ index: control.options.findIndex(option => option.value === value) })
        }
        await filter('queries-items_col', 'ok')
        await expect(rows('items_col')).toHaveCount(0)
        await filter('queries-items_col', 'defect')
        await expect(rows('items_col').filter({ hasText: marker })).toHaveCount(1)
        await filter('queries-items_col', '')
        await filter('queries-inspections_col', 'draft')
        await expect(rows('inspections_col')).toHaveCount(0)
        await filter('queries-inspections_col', 'completed')
        await expect(rows('inspections_col').filter({ hasText: marker })).toHaveCount(1)
        await filter('queries-inspections_col', '')
        const search = host('queries-inspections_col').locator('input[type=search]')
        await search.fill(marker + '-missing')
        await expect(rows('inspections_col')).toHaveCount(0)
        await search.fill(marker)
        await expect(rows('inspections_col').filter({ hasText: marker })).toHaveCount(1)
        await search.fill('')
      })
      await checkpoint('changing-parent-clears-dependent-selection', async () => {
        await command('create-asset').click()
        await fill('asset_editor', 'name', marker + '-empty')
        await submit('asset_editor', 'save-asset')
        await rows('assets_col').filter({ hasText: marker + '-empty' }).click()
        await expect(rows('inspections_col')).toHaveCount(0)
        await expect(rows('items_col')).toHaveCount(0)
        await expect(command('create-item')).toBeDisabled()
        await expect(command('create-inspection')).toBeEnabled()
      })
    } catch (error) {
      sample.failure = String(error)
      console.log(`${layout}: ${sample.active} failed: ${error.message}`)
      // Observe outstanding reads after failure; never upgrade the failed verdict.
      sample.pendingAtFailure = [...pendingReads].map(request => ({ tool: request.postDataJSON()?.tool,
        args: request.postDataJSON()?.arguments, elapsedMs: Date.now() - startedAt.get(request) }))
      const deadline = Date.now() + 15_000
      while (pendingReads.size && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 100))
    } finally {
      sample.alertDismissals = await page.evaluate(() => window.__E2E_ALERT_DISMISS__)
      sample.runtimeEvents = await page.evaluate(() => window.__ADAOS_RUNTIME_DEBUG__?.get?.())
      sample.collectionDiagnostics = await page.locator('ada-list-widget,ada-table-widget').evaluateAll(elements => elements.map(element => {
        const c = window.ng?.getComponent(element)
        return { id: c?.widget?.id, state: c?.state?.getSnapshot?.(), items: c?.items, rows: c?.rows }
      }))
      sample.formDiagnostics = await page.locator('ada-form-widget').evaluateAll(elements => elements.map(element => {
        const c = window.ng?.getComponent(element)
        return { id: c?.widget?.id, submitting: c?.submitting, values: c?.values, errors: c?.errors }
      }))
      await page.screenshot({ path: path.join(output, `${layout}-final.png`), fullPage: true }).catch(() => {})
      await fs.writeFile(path.join(output, `${layout}-body.txt`), await page.locator('body').innerText(), 'utf8')
      await context.close()
      await Promise.allSettled(responseReads)
      await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
    }
  }
} finally { await browser.close() }
if (report.samples.some(sample => sample.failure || sample.errors.length)) process.exitCode = 1
