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
    const sample = { layout, checks: [], errors: [], operations: [] }
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(error.message))
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/resources/operate') sample.operations.push(request.postDataJSON())
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
          await host(settings.id).locator(`[data-command-id=${JSON.stringify(button.id)}]`).click()
          await expect(page.locator('ion-modal').last()).toBeVisible()
          await expect(page.locator('ion-modal').last().locator('ada-page-widget-host').first()).toBeVisible()
          await page.screenshot({ path: path.join(output, `${layout}-settings.png`), fullPage: true })
          await page.locator('ion-modal').last().getByRole('button', { name: /Close|Закрыть/, exact: true }).click()
          sample.checks.push({ kind: 'settings-open-close', section: button.id })
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
