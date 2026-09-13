// Independent retained-candidate probe; never part of a generation prompt.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const env = process.env
const pin = JSON.parse(await fs.readFile(env.ADAOS_E2E_SNAPSHOT_PIN, 'utf8'))
const app = JSON.parse(await fs.readFile(env.ADAOS_E2E_EXPECTED_WEBUI, 'utf8')).ui.application
const scenario = env.ADAOS_E2E_SCENARIO_ID
const webspace = env.ADAOS_E2E_WEBSPACE_ID
const output = env.ADAOS_E2E_OUTPUT
if (env.ENV_TYPE !== 'dev' || pin.stage !== 'automation' || pin.scenario_id !== scenario
  || !scenario.startsWith('test_') || !webspace.startsWith('preview-')) throw new Error('Pinned TEST Automation only')
const widgets = [...app.desktop.pageSchema.widgets, ...Object.values(app.modals).flatMap(m => m.schema.widgets)]
const forms = widgets.filter(w => w.type === 'ui.form')
const aliases = {}
for (const [kind, alias] of [['assets', 'create-asset-form'], ['inspections', 'create-inspection-form'], ['inspection_items', 'create-item-form']]) {
  const matches = forms.filter(f => !f.dataSource && f.actions?.some(a => (a.params?.kind ?? a.params?.entity) === kind && a.target?.endsWith('.save_record')))
  if (matches.length !== 1) throw new Error(`Review the create-form binding for ${kind}; found ${matches.length}`)
  aliases[alias] = matches[0].id
}
const commandAliases = {}
for (const [key, formAlias] of [['asset-actions:create-asset', 'create-asset-form'], ['asset-actions:edit', 'asset_editor'],
  ['open-inspection_editor:create-inspection', 'create-inspection-form'], ['item-actions:create-item', 'create-item-form']]) {
  const formId = aliases[formAlias] ?? formAlias
  const modal = Object.entries(app.modals).find(([, m]) => m.schema.widgets.some(w => w.id === formId))?.[0]
  const matches = widgets.flatMap(w => (w.actions ?? []).filter(a => a.type === 'openModal' && a.params?.modalId === modal
    && a.on?.startsWith('click:')).map(a => [w.id, a.on.slice(6)]))
  if (matches.length !== 1) throw new Error(`Review the explicit command for ${formAlias}`)
  commandAliases[key] = matches[0]
}
const report = { scope: 'DEV owner, independent repeat lifecycle; not delegated access or concurrent-editor acceptance',
  task: pin.revision, scenario, discoveredBindings: { aliases, commandAliases }, samples: [] }
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const sample = { layout, checks: [], errors: [], network: [] }
    report.samples.push(sample)
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-repeat', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
    }, { hub: env.ADAOS_E2E_HUB_URL, token: env.ADAOS_E2E_HUB_TOKEN, subnet: env.ADAOS_E2E_SUBNET_ID, webspace })
    const page = await context.newPage()
    const requests = new Map()
    const responseReads = []
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/tools/call') requests.set(request, Date.now())
    })
    page.on('response', response => {
      const request = response.request()
      if (!requests.has(request)) return
      const body = request.postDataJSON()
      const entry = { tool: body?.tool, args: body?.arguments, status: response.status(),
        elapsedMs: Date.now() - requests.get(request), step: sample.active }
      sample.network.push(entry)
      responseReads.push(response.json().then(body => { entry.ok = body.ok !== false && body.result?.ok !== false }).catch(() => {}))
    })
    page.on('pageerror', error => sample.errors.push(error.message))
    const url = new URL(env.ADAOS_E2E_CLIENT_URL)
    for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: env.ADAOS_E2E_SUBNET_ID,
      webspace_id: webspace, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(aliases[id] ?? id)}]`).last()
    const rows = id => host(id).locator('tr.row-selectable,.collection-focus-item')
    const field = (form, id) => host(form).locator(`[data-webui-field-id=${JSON.stringify(id)}]`)
    const buttonId = (form, id) => id === 'save'
      ? forms.find(f => f.id === (aliases[form] ?? form))?.actions.find(a => a.target?.endsWith('.save_record'))?.id ?? id : id
    const command = (widget, id) => {
      const [target, button] = commandAliases[`${widget}:${id}`] ?? [widget, buttonId(widget, id)]
      return host(target).locator(`[data-command-id=${JSON.stringify(button)}]`)
    }
    const fill = (form, id, text) => field(form, id).locator('input,textarea').fill(text)
    const ready = () => page.waitForFunction(id => {
      const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
      return sync?.providerSynced && sync?.materializationReady && sync?.materialization?.currentScenario === id
    }, scenario, { timeout: 60_000 })
    const dismiss = async () => {
      const modal = page.locator('ion-modal').last()
      await modal.getByRole('button', { name: /Close|Закрыть/, exact: true }).click()
      await expect(modal).not.toBeVisible()
    }
    const check = async (id, work) => { sample.active = id; await work(); sample.checks.push(id); sample.active = null }
    const submit = async (form, button, success = true) => {
      const pending = page.waitForResponse(r => new URL(r.url()).pathname === '/api/tools/call'
        && r.request().postDataJSON()?.tool?.endsWith(':save_record'))
      void pending.catch(() => {})
      await command(form, button).click()
      const confirmation = forms.find(f => f.id === (aliases[form] ?? form))?.actions.find(a => a.id === buttonId(form, button))?.confirmation
      if (confirmation) await page.locator('ion-alert').last().getByRole('button', { name: confirmation.confirmLabel, exact: true }).click()
      const response = await pending
      const body = await response.json()
      const result = body.result ?? body
      expect(response.ok() && body.ok !== false && result.ok !== false).toBe(success)
      if (success) await expect(host(form)).toHaveCount(0)
      else await expect(host(form)).toBeVisible()
      return result
    }
    const parent = async (form, id, caption) => {
      const select = field(form, id).locator('select')
      const option = select.locator('option', { hasText: caption })
      await expect(option).toHaveCount(1)
      const declaration = forms.find(f => f.id === (aliases[form] ?? form)).inputs.fields.find(f => f.id === id)
      if (declaration.readOnly || declaration.readonly) {
        await expect(select).toHaveValue(await option.getAttribute('value'))
        await expect(select.locator('option:checked')).toContainText(caption)
      } else {
        await expect(select).toBeEnabled()
        await select.selectOption({ label: (await option.innerText()).trim() })
      }
    }
    const marker = `E2E-REPEAT-${layout}-${Date.now()}`
    sample.marker = marker
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await ready()
      await check('create-equipment', async () => {
        await command('asset-actions', 'create-asset').click()
        await fill('create-asset-form', 'name', marker)
        await fill('create-asset-form', 'location', 'E2E location')
        await submit('create-asset-form', 'save')
        await expect(rows('assets_col').filter({ hasText: marker })).toHaveCount(1)
      })
      await check('select-edit-cancel', async () => {
        await rows('assets_col').filter({ hasText: marker }).click()
        await command('asset-actions', 'edit').click()
        await fill('asset_editor', 'location', 'Updated location')
        await submit('asset_editor', 'save')
        await command('asset-actions', 'edit').click()
        await fill('asset_editor', 'location', 'Discarded')
        await dismiss()
        await command('asset-actions', 'edit').click()
        await expect(field('asset_editor', 'location').locator('input')).toHaveValue('Updated location')
        await dismiss()
      })
      await check('inspection-with-visible-parent', async () => {
        await command('open-inspection_editor', 'create-inspection').click()
        await parent('create-inspection-form', 'asset_id', marker)
        await fill('create-inspection-form', 'started_at', '2026-09-13')
        await fill('create-inspection-form', 'note', marker)
        await page.screenshot({ path: path.join(output, `${layout}-parent.png`), fullPage: true })
        await submit('create-inspection-form', 'save')
        await rows('inspections_col').filter({ hasText: marker }).click()
      })
      await check('empty-completion-rejected', async () => {
        await command('open-inspection_editor', 'edit').click()
        await submit('inspection_editor', 'cmd_complete_inspection', false)
        await expect(field('inspection_editor', 'note').locator('textarea')).toHaveValue(marker)
        await dismiss()
      })
      await check('defect-draft-without-comment', async () => {
        await command('item-actions', 'create-item').click()
        await parent('create-item-form', 'inspection_id', marker)
        await fill('create-item-form', 'title', marker + '-item')
        const options = forms.find(f => f.id === aliases['create-item-form']).inputs.fields.find(f => f.id === 'result').options
        await field('create-item-form', 'result').locator('input[type=radio]').nth(options.findIndex(o => o.value === 'defect')).check()
        await submit('create-item-form', 'save')
        await expect(rows('items_col').filter({ hasText: marker })).toHaveCount(1)
      })
      await check('defect-completion-rejected', async () => {
        await command('open-inspection_editor', 'edit').click()
        await submit('inspection_editor', 'cmd_complete_inspection', false)
        await dismiss()
      })
      await check('invalid-link-retains-draft', async () => {
        await rows('items_col').filter({ hasText: marker }).click()
        await fill('item_editor', 'comment', 'Observed defect')
        await fill('item_editor', 'defect_photo', 'http://example.org/photo.jpg')
        await submit('item_editor', 'cmd_save_item', false)
        await expect(field('item_editor', 'comment').locator('textarea')).toHaveValue('Observed defect')
        await fill('item_editor', 'defect_photo', 'https://example.org/photo.jpg')
        await submit('item_editor', 'cmd_save_item')
      })
      await check('complete-lock-reload', async () => {
        await command('open-inspection_editor', 'edit').click()
        expect((await submit('inspection_editor', 'cmd_complete_inspection')).item.status).toBe('completed')
        await page.reload({ waitUntil: 'domcontentloaded' })
        await ready()
        await rows('assets_col').filter({ hasText: marker }).click()
        await rows('inspections_col').filter({ hasText: marker }).click()
        await rows('items_col').filter({ hasText: marker }).click()
        await expect(field('item_editor', 'comment').locator('textarea')).toBeDisabled()
        await expect(field('item_editor', 'defect_photo').locator('input')).toHaveValue('https://example.org/photo.jpg')
        await dismiss()
      })
    } catch (error) { sample.failure = String(error) }
    finally {
      await page.screenshot({ path: path.join(output, `${layout}-final.png`), fullPage: true }).catch(() => {})
      await fs.writeFile(path.join(output, `${layout}-body.txt`), await page.locator('body').innerText(), 'utf8')
      await context.close()
      await Promise.allSettled(responseReads)
      await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
      console.log(layout, sample.checks.length, sample.active, sample.failure ?? 'passed')
    }
  }
} finally { await browser.close() }
if (report.samples.some(s => s.failure || s.errors.length)) process.exitCode = 1
