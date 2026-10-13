import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { isDeepStrictEqual } from 'node:util'
import { installSystemOverlayDismissal, revealPrototypeWidget, prototypeRowSelector, selectPrototypeRowForCommand, recordEditorOpener, prototypeRowCommand } from './prototype-navigation.mjs'
import { canonicalWidgetSources, canonicalRenderedWidgetSources } from './widget-source-parity.mjs'
import { observeUsability } from './prototype-usability.mjs'

const expect = baseExpect.configure({ timeout: 30_000 })

// CSS visibility alone admits text clipped by an inner table scroller. Check
// the actual glyph rectangles against every clipping ancestor and the viewport.
async function visibleTextGeometry(locator) {
  return locator.evaluate(element => {
    const clip = { left: 0, top: 0, right: innerWidth, bottom: innerHeight }
    for (let node = element.parentElement; node; node = node.parentElement) {
      const style = getComputedStyle(node)
      const box = node.getBoundingClientRect()
      if (/auto|scroll|hidden|clip/.test(style.overflowX)) {
        clip.left = Math.max(clip.left, box.left + node.clientLeft)
        clip.right = Math.min(clip.right, box.left + node.clientLeft + node.clientWidth)
      }
      if (/auto|scroll|hidden|clip/.test(style.overflowY)) {
        clip.top = Math.max(clip.top, box.top + node.clientTop)
        clip.bottom = Math.min(clip.bottom, box.top + node.clientTop + node.clientHeight)
      }
    }
    const range = document.createRange()
    range.selectNodeContents(element)
    const rects = [...range.getClientRects()].filter(rect => rect.width > 0 && rect.height > 0)
    const visible = rects.length > 0 && rects.every(rect => rect.left >= clip.left - 1
      && rect.right <= clip.right + 1 && rect.top >= clip.top - 1 && rect.bottom <= clip.bottom + 1)
    const scroller = element.closest('.table-wrap')
    return { visible, clip, rects: rects.map(rect => ({ left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom })),
      scrollLeft: scroller?.scrollLeft, scrollWidth: scroller?.scrollWidth, clientWidth: scroller?.clientWidth }
  })
}

if (process.env.ENV_TYPE !== 'dev') throw new Error('Semantic browser probe requires DEV')
const scenario = process.env.ADAOS_E2E_SCENARIO_ID
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const token = process.env.ADAOS_E2E_HUB_TOKEN
const hub = process.env.ADAOS_E2E_HUB_URL
if (!scenario?.startsWith('test_semantic_') || !webspace || !subnet || !token || !hub) throw new Error('Owned semantic fixture identity required')
const source = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_EXPECTED_WEBUI, 'utf8'))
const widgets = source.ui.application.desktop.pageSchema.widgets
const collection = widgets.find(widget => widget.id === 'records-list')
const modal = Object.entries(source.ui.application.modals || {}).find(([, value]) =>
  value.schema?.widgets?.some(widget => widget.type === 'ui.form' && widget.actions?.some(action => action.params?.operation_id === 'update')))
const editor = modal?.[1].schema.widgets.find(widget => widget.type === 'ui.form')
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output, { recursive: true })
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL)
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: webspace, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
const report = { ok: false, scenario, webspace, expectedView: process.env.ADAOS_E2E_LABEL_VIEW,
  expectedField: process.env.ADAOS_E2E_LABEL_FIELD, samples: [], modelCalls: 0 }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: `e2e-semantic:${webspace}`, adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en' })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace })
    const page = await context.newPage()
    page.setDefaultTimeout(30_000)
    const sample = { layout, checks: [], errors: [] }
    report.samples.push(sample)
    await installSystemOverlayDismissal(page, () => {})
    page.on('pageerror', error => sample.errors.push(String(error)))
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync.materialization.currentScenario === expected
      }, scenario, { timeout: 60_000 })
      const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).first()
      await revealPrototypeWidget(page, widgets, collection)
      await expect(host(collection.id)).toContainText('First task')
      await expect(host(collection.id)).toContainText(report.expectedView)
      await expect(host(collection.id)).toContainText(report.expectedField)
      await expect(host(collection.id)).toContainText('Alpha')
      sample.checks.push('live records', 'view label', 'field label', 'related display')
      if (process.env.ADAOS_E2E_COLUMN_ORDER) {
        const expectedColumns = JSON.parse(process.env.ADAOS_E2E_COLUMN_ORDER)
        const table = host(collection.id).locator('ada-table-widget').first()
        const actualColumns = await table.evaluate(element => window.ng.getComponent(element).columns.map(column => column.label))
        if (!isDeepStrictEqual(actualColumns, expectedColumns)) throw new Error(`Wrong compiled column order: ${JSON.stringify(actualColumns)}`)
        // Check rendered labels too; semantic source equality is not a browser proof.
        for (const label of expectedColumns) await expect(table).toContainText(label)
        sample.columnOrder = actualColumns
        sample.checks.push('exact column order')
      }
      const primaryText = host(collection.id).locator('tbody tr').first()
        .locator('td .cell-text').filter({ hasText: /^First task$/ }).first()
      const primaryReadyStarted = performance.now()
      // Live projection replacement can momentarily leave a cell empty after
      // another assertion found the old row. Wait for the exact record glyphs;
      // persistent clipping still fails the same geometry condition.
      await expect.poll(async () => {
        sample.primaryBeforeEditor = await visibleTextGeometry(primaryText)
        return sample.primaryBeforeEditor.visible
      }, { message: 'Primary record text is clipped before opening its editor' }).toBe(true)
      sample.primaryTextReadyMs = Math.round(performance.now() - primaryReadyStarted)
      const rendered = await page.locator('ada-page-widget-host').evaluateAll(elements => elements.map(element => window.ng?.getComponent(element)?.widget).filter(Boolean))
      const present = new Set(rendered.map(widget => widget.id))
      if (!isDeepStrictEqual(canonicalRenderedWidgetSources(rendered, widgets), canonicalWidgetSources(widgets.filter(widget => present.has(widget.id))))) {
        throw new Error('Rendered widget source identities differ from selected fixture')
      }
      sample.checks.push('source identity')
      if (editor) {
        const row = host(collection.id).locator(prototypeRowSelector(collection)).first()
        await selectPrototypeRowForCommand(page, widgets, collection, row)
        const opener = recordEditorOpener(widgets, modal[0], editor.inputs.selectedStateKey)
        if (!opener) throw new Error('Existing record editor is not reachable')
        await (opener.collection ? prototypeRowCommand(row, opener.commandId)
          : host(opener.owner.id).locator(`[data-command-id=${JSON.stringify(opener.commandId)}]`)).click()
        const form = host(editor.id)
        await expect(form).toBeVisible()
        await expect(form).toContainText(report.expectedField)
        const nameInput = form.locator('[data-webui-field-id="name"] input').first()
        await expect(nameInput).toHaveValue('First task')
        await nameInput.fill('Unsubmitted name change')
        await page.keyboard.press('Escape')
        await expect(form).toBeHidden()
        await expect(host(collection.id)).toContainText('First task')
        sample.checks.push('editor reachability', 'cancel preserves record')
        const cancelReadyStarted = performance.now()
        await expect.poll(async () => {
          sample.primaryAfterCancel = await visibleTextGeometry(primaryText)
          return sample.primaryAfterCancel.visible
        }, { message: 'Primary record text is clipped after cancelling its editor' }).toBe(true)
        sample.primaryAfterCancelReadyMs = Math.round(performance.now() - cancelReadyStarted)
        sample.checks.push('primary record remains visually readable')
      } else {
        throw new Error('Fixture requires a separate update editor')
      }
      sample.locale = await page.evaluate(() => localStorage.getItem('adaos_lang'))
      sample.usability = await observeUsability(page, { compact: layout === 'compact' })
      if (!sample.usability.ok) throw new Error('Semantic fixture usability floor failed')
      if (sample.errors.length) throw new Error('Runtime page errors')
      sample.ok = true
    } catch (error) {
      sample.ok = false
      sample.error = String(error)
      await fs.writeFile(path.join(output, `${layout}-body.txt`), await page.locator('body').innerText())
    }
    await page.screenshot({ path: path.join(output, `${layout}.png`), fullPage: true })
    await context.close()
  }
  report.ok = report.samples.every(sample => sample.ok)
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
}
console.log(JSON.stringify({ ok: report.ok, samples: report.samples.map(sample => ({ layout: sample.layout, ok: sample.ok, error: sample.error })) }))
if (!report.ok) process.exitCode = 1
