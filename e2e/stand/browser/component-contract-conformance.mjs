// Independent behavioral obligations on the real renderer; no compiler oracle.
import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal, prototypeRowSelector, prototypeRowCommand, revealPrototypeWidget } from './prototype-navigation.mjs'
import { assertInvalidSubmissionGuard } from './prototype-validation-guard.mjs'

const expect = baseExpect.configure({ timeout: 30_000 })
const scenario = process.env.ADAOS_E2E_SCENARIO_ID
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const output = process.env.ADAOS_E2E_OUTPUT
if (process.env.ENV_TYPE !== 'dev' || !scenario?.startsWith('test_semantic_') || !webspace?.endsWith('-dev')) throw new Error('Owned DEV fixture required')
const source = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_EXPECTED_WEBUI, 'utf8'))
const widgets = source.ui.application.desktop.pageSchema.widgets
const collection = widgets.find(widget => widget.id === 'records-list')
const report = { ok: false, scenario, webspace, modelCalls: 0, samples: [] }
const browser = await chromium.launch({ headless: true })
try {
  await fs.mkdir(output, { recursive: true })
  for (const [layout, viewport] of [['wide', { width: 1440, height: 1000 }], ['compact', { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      for (const [key, value] of Object.entries({ adaos_device_id: `contract-e2e:${webspace}`, adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_hub_url: hub, adaos_local_hub_token: token, adaos_local_subnet_id: subnet,
        adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en' })) localStorage.setItem(key, value)
    }, { hub: process.env.ADAOS_E2E_HUB_URL, token: process.env.ADAOS_E2E_HUB_TOKEN,
      subnet: process.env.ADAOS_E2E_SUBNET_ID, webspace })
    const page = await context.newPage()
    const sample = { layout, checks: [], errors: [], mutations: [] }
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(String(error)))
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/resources/operate') {
        const body = request.postDataJSON()
        sample.mutations.push({ operation: body.operation_id, command: body.context?.prototype_command_id })
      }
    })
    await installSystemOverlayDismissal(page, () => {})
    const url = new URL(process.env.ADAOS_E2E_CLIENT_URL)
    for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: process.env.ADAOS_E2E_SUBNET_ID,
      webspace_id: webspace, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).filter({ visible: true }).first()
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync.materialization.currentScenario === expected
      }, scenario, { timeout: 60_000 })
      await revealPrototypeWidget(page, widgets, collection)
      const table = host('records-list')
      await expect(table).toContainText('First task')
      await expect(table).not.toContainText('Second task')
      sample.checks.push('permanent-scope')
      const query = host('queries-records-list')
      const search = query.locator('input[type=search]')
      await search.fill('no-such-record')
      await expect(table).not.toContainText('First task')
      await query.locator('.query-toolbar__reset').click()
      await expect(table).toContainText('First task')
      await expect(table).not.toContainText('Second task')
      await expect(search).toHaveValue('')
      sample.checks.push('reset-preserves-scope')
      const row = table.locator(prototypeRowSelector(collection)).filter({ hasText: 'First task' }).first()
      await prototypeRowCommand(row, 'edit').click()
      const form = host('record-editor')
      await expect(form).toBeVisible()
      const name = form.locator('input[id="form-record-editor-name"]')
      const note = form.locator('input[id="form-record-editor-review_note"]')
      await expect(name).toHaveValue('First task')
      await expect(note).toHaveValue('')
      const complete = form.locator('[data-command-id="complete"]').locator('button').or(form.locator('button[data-command-id="complete"]')).first()
      await expect(complete).toBeDisabled()
      sample.checks.push('guard-rejected-draft')
      await name.fill('')
      const save = form.locator('[data-command-id="save"]').locator('button').or(form.locator('button[data-command-id="save"]')).first()
      sample.invalidSubmissionMode = await assertInvalidSubmissionGuard({ button: save,
        error: form.locator('.field-error').first(), settle: async () => {}, mutationCount: () => sample.mutations.length })
      sample.checks.push('required-rejected-draft')
      await page.locator('ion-modal').filter({ visible: true }).last().getByRole('button', { name: /^(Close|Закрыть)$/ }).click()
      await expect(table).toContainText('First task')
      await prototypeRowCommand(row, 'edit').click()
      await expect(name).toHaveValue('First task')
      await expect(note).toHaveValue('')
      await note.fill('Contract approved')
      await expect(complete).toBeEnabled()
      await complete.click()
      const alert = page.locator('ion-alert').last()
      await expect(alert).toBeVisible()
      await alert.locator('button').first().click()
      await expect(page.locator('ion-alert')).toHaveCount(0)
      await expect(note).toHaveValue('Contract approved')
      if (sample.mutations.length !== 0) throw new Error('Cancel/invalid/confirmation cancel caused a mutation')
      sample.checks.push('cancel-and-confirmation-no-write')
      await page.locator('ion-modal').filter({ visible: true }).last().getByRole('button', { name: /^(Close|Закрыть)$/ }).click()
      await prototypeRowCommand(row, 'edit').click()
      await expect(note).toHaveValue('')
      sample.checks.push('draft-isolation-on-reopen')
      if (sample.errors.length) throw new Error('Renderer page errors')
      sample.ok = true
    } catch (error) {
      sample.ok = false
      sample.error = String(error)
      await fs.writeFile(path.join(output, layout + '-body.txt'), await page.locator('body').innerText())
    }
    await page.screenshot({ path: path.join(output, layout + '.png'), fullPage: true })
    await context.close()
  }
  report.ok = report.samples.every(sample => sample.ok)
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
}
console.log(JSON.stringify({ ok: report.ok, samples: report.samples.map(({ layout, ok, error }) => ({ layout, ok, error })) }))
if (!report.ok) process.exitCode = 1
