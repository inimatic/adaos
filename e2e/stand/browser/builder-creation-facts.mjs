import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { prototypeRowById, prototypeFieldCell, revealPrototypeWidget,
  installSystemOverlayDismissal, dismissSystemOverlays } from './prototype-navigation.mjs'

const input = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CREATION_INPUT, 'utf8'))
const { project, accepted, expected } = input
assert(/^test_creation_[a-f0-9]{18}$/.test(project))
assert.equal(accepted.project, project)
const output = process.env.ADAOS_E2E_OUTPUT
await fs.mkdir(output, { recursive: true })
const semantic = accepted.semantic_document
const widgets = accepted.webui.ui.application.desktop.pageSchema.widgets
const report = { ok: false, project, modelCalls: 0, operations: [], samples: [] }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, width, height] of [['wide', 1440, 1000], ['compact', 390, 844]]) {
    const sample = { layout, checks: [], errors: [], queries: [] }
    report.samples.push(sample)
    const context = await browser.newContext({ viewport: { width, height }, locale: 'ru-RU' })
    await context.addInitScript(({ token, hub, subnet }) => {
      if (!/^https?:$/.test(location.protocol)) return
      for (const [key, value] of Object.entries({ adaos_local_hub_url: hub,
        adaos_local_hub_token: token, adaos_local_subnet_id: subnet,
        adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
    }, { token: process.env.ADAOS_E2E_HUB_TOKEN, hub: process.env.ADAOS_E2E_HUB_URL, subnet: process.env.ADAOS_E2E_SUBNET_ID })
    const page = await context.newPage()
    page.setDefaultTimeout(20_000)
    await installSystemOverlayDismissal(page)
    page.on('pageerror', error => sample.errors.push(String(error)))
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/resources/operate') report.operations.push({ mutation: true })
    })
    page.on('response', async response => {
      if (new URL(response.url()).pathname !== '/api/resources/query') return
      const request = response.request().postDataJSON()
      if (!String(request?.resource_type || '').startsWith('prototype.project.' + project + '.')) return
      sample.queries.push({ resource_type: request.resource_type, status: response.status(),
        response: await response.json().catch(() => null) })
    })
    try {
      const url = new URL(process.env.ADAOS_E2E_CLIENT_URL)
      for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo',
        subnet_id: process.env.ADAOS_E2E_SUBNET_ID, webspace_id: input.preview_webspace_id,
        scenario_id: project, expected_scenario_id: project, expected_revision: '001', try_local_hub: '1' })) url.searchParams.set(key, value)
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await expect(page.locator('.scenario-release-stage')).toHaveText('ALPHA', { timeout: 60_000 })
      const materialization = await page.evaluate(() => {
        const c = window.ng.getComponent(document.querySelector('app-root'))
        return c.ydoc.toJSON(c.ydoc.getPath('ui/application/desktop/pageSchema'))._adaos.materialization
      })
      assert.equal(materialization.stage, 'prototype')
      assert.equal(materialization.revision, '001')
      sample.materialization = materialization
      for (const record of expected.records) {
        for (const [sourceField, expectedValue] of Object.entries(record.fields)) {
          const facts = input.task_contract.facts.filter(fact => fact.subject === record.subject
            && fact.source_field === sourceField)
          assert.equal(facts.length, 1, 'Independent source slot must be present in admitted meaning')
          assert.deepEqual(facts[0].value, expectedValue)
          const locator = semantic.requirement_bindings.find(row => row.requirement_ref === facts[0].assertion_ref)
          assert.equal(locator?.semantic_refs.length, 1)
          const fieldId = locator.semantic_refs[0].replace(/^field:/, '')
          const resources = semantic.resources.filter(resource => resource.fields.some(field => field.id === fieldId))
          assert.equal(resources.length, 1)
          const resource = resources[0]
          const records = resource.records.filter(row => row.id === record.subject || Object.values(row).includes(record.subject))
          assert.equal(records.length, 1)
          const view = semantic.views.find(view => view.role === 'collection' && view.presentation === 'table'
            && view.resource_ref === resource.id && view.field_refs.includes(fieldId))
          assert(view, 'All source fields must have the requested table projection')
          const widget = widgets.find(widget => widget.id === view.id && widget.type === 'ui.table')
          assert(widget, 'No supported table renderer for the admitted view')
          await dismissSystemOverlays(page)
          await revealPrototypeWidget(page, widgets, widget)
          const host = page.locator(`[data-webui-widget-id=${JSON.stringify(widget.id)}]`).filter({ visible: true }).last()
          const row = await prototypeRowById(host, widget, String(records[0].id))
          const { value } = await prototypeFieldCell(row, widget, fieldId)
          const receipt = sample.queries.filter(query => query.resource_type.endsWith('.' + resource.id)
            && query.response?.items?.some(item => String(item.id) === String(records[0].id))).at(-1)
          assert.equal(receipt?.status, 200)
          const observed = receipt.response.items.find(item => String(item.id) === String(records[0].id))
          assert.deepEqual(observed[fieldId], expectedValue, 'Live query must retain the independently expected scalar')
          const text = (await value.innerText()).trim()
          if (typeof expectedValue === 'boolean') {
            const checkbox = value.locator('ion-checkbox')
            await expect(checkbox).toBeVisible()
            assert.equal(await checkbox.evaluate(element => element.checked), expectedValue)
          } else if (typeof expectedValue === 'number') {
            assert.equal(Number(text.replace(/[\s\u00a0]/g, '').replace(',', '.')), expectedValue)
          } else if (expectedValue === null) {
            assert(['', '-', '—', 'null'].includes(text), 'Null must not render invented nonempty content')
          } else assert.equal(text, expectedValue)
          sample.checks.push({ subject: record.subject, field: sourceField, visible: true, queryValueMatches: true })
        }
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2), true)
      await page.screenshot({ path: path.join(output, layout + '.png'), fullPage: true })
    } catch (error) {
      sample.errors.push(String(error))
      await page.screenshot({ path: path.join(output, layout + '-failure.png'), fullPage: true }).catch(() => {})
    }
    await context.close()
  }
  report.ok = report.operations.length === 0 && report.samples.every(sample => !sample.errors.length
    && sample.checks.length === expected.records.reduce((count, row) => count + Object.keys(row.fields).length, 0))
} finally {
  await fs.writeFile(path.join(output, 'functional-browser.json'), JSON.stringify(report, null, 2), { flag: 'wx' })
  await browser.close()
}
console.log(JSON.stringify({ ok: report.ok, samples: report.samples.map(({ layout, checks, errors }) => ({ layout, checks: checks.length, errors })) }))
if (!report.ok) process.exitCode = 1
