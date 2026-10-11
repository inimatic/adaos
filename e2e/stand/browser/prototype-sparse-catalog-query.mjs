// Held-out catalog task assertions; never included in a model's input.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal, revealPrototypeWidget } from './prototype-navigation.mjs'

if (process.env.ENV_TYPE !== 'dev') throw new Error('DEV only')
const checkpoint = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CHECKPOINT, 'utf8'))
const created = checkpoint.steps.find(step => step.id === 'create')?.output
const scenario = created?.scenario_id
const ownership = checkpoint.cleanup
const preview = ownership?.previews?.find(item => item.scenario_id === scenario && item.test && item.stage === 'prototype')
if (!ownership?.test || !['retained_for_review', 'review_in_progress'].includes(ownership.status)
  || ownership.acceptance !== 'not_approved' || !scenario?.startsWith('test_') || !preview?.webspace_id?.startsWith('test_')
  || !ownership.owned_artifacts?.some(item => item.primary_ref === `scenario:${scenario}` && item.project_id === scenario)) throw new Error('Owned unapproved test only')
const application = JSON.parse(await fs.readFile(path.join(created.artifact_root, 'webui.json'), 'utf8')).ui.application
const widgets = application.desktop.pageSchema.widgets
const collection = widgets.find(widget => widget.type === 'ui.table')
const toolbar = widgets.find(widget => widget.type === 'ui.queryToolbar' && widget.inputs.controls.some(control => control.kind === 'search'))
if (!collection || !toolbar) throw new Error('Task requires a table and searchable public controls')
const hub = process.env.ADAOS_E2E_HUB_URL
const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
if (!hub || !token || !subnet) throw new Error('Explicit transport identity required')
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output, { recursive: true })
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: preview.webspace_id, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
const browser = await chromium.launch({ headless: true })
const report = { scenario, passed: false, samples: [], scope: 'Public search/no-results/reset and genre filtering versus independent durable resource snapshot; read-only domain operations' }
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true; window.__ADAOS_BASE__ = hub; window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: `e2e-catalog:${webspace}`, adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace: preview.webspace_id })
    const page = await context.newPage()
    const sample = { layout, checks: [], errors: [], operations: [] }
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(error.message))
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/resources/operate') sample.operations.push({ method: request.method() }) })
    await installSystemOverlayDismissal(page)
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
    const ids = () => host(collection.id).locator('ada-table-widget').evaluate(element => window.ng.getComponent(element).rows.map(row => row.id).sort())
    try {
      const response = await context.request.post(hub + '/api/resources/query', { headers: { 'X-AdaOS-Token': token },
        data: { resource_type: collection.dataSource.resourceType, limit: 100 } })
      const snapshot = await response.json()
      if (response.status() !== 200 || !snapshot.ok || snapshot.items.length !== 4) throw new Error('Task requires exactly four durable example books')
      const allIds = snapshot.items.map(item => item.id).sort()
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => { const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync;
        return sync?.materializationReady && sync.materialization?.currentScenario === expected }, scenario, { timeout: 60000 })
      await revealPrototypeWidget(page, widgets, collection)
      await expect.poll(ids, { timeout: 30000 }).toEqual(allIds)
      await revealPrototypeWidget(page, widgets, toolbar)
      const search = toolbar.inputs.controls.find(control => control.kind === 'search')
      await host(toolbar.id).locator(`[data-query-id=${JSON.stringify(search.id)}] input`).fill('e2e-no-book-with-this-title-3d974a')
      await expect.poll(ids, { timeout: 30000 }).toEqual([])
      await host(toolbar.id).locator('.query-toolbar__reset').click()
      await expect.poll(ids, { timeout: 30000 }).toEqual(allIds)
      sample.checks.push('public-search/no-results/reset')
      const genre = toolbar.inputs.controls.find(control => control.kind === 'filter' && /genre|жанр/i.test(JSON.stringify(control)))
      if (!genre) throw new Error('Missing public genre filter')
      const toggle = host(toolbar.id).locator('.query-toolbar__toggle')
      if (await toggle.getAttribute('aria-expanded') === 'false') await toggle.click()
      const declaredField = Object.entries(collection.dataSource.query.filters || {}).find(([, value]) => value === `$state.${genre.stateKey}`)?.[0]
      if (!declaredField) throw new Error('Genre filter lacks a declared resource field binding')
      const valueFor = item => item[declaredField] ?? item.attributes?.[declaredField]
      const genres = [...new Set(snapshot.items.map(valueFor))]
      if (genres.length !== 2) throw new Error('Task requires two example genres')
      const field = host(toolbar.id).locator(`[data-query-id=${JSON.stringify(genre.id)}]`)
      const select = field.locator('select')
      const options = await select.locator('option').evaluateAll(nodes => nodes.map(node => ({ value: node.value, text: node.textContent })))
      // Angular ngValue encodes DOM option values. Pair by the declared public
      // option order; do not mutate Angular state or call the resource filter API.
      const declaredOptions = await host(toolbar.id).locator('ada-query-toolbar-widget').evaluate((element, id) =>
        window.ng.getComponent(element).controls.find(control => control.id === id).options, genre.id)
      if (options.length !== declaredOptions.length) throw new Error('Public option mapping mismatch')
      const selectedIndex = declaredOptions.findIndex(option => genres.includes(option.value))
      const selected = selectedIndex >= 0 && declaredOptions[selectedIndex]
      if (!selected) throw new Error('Genre choices are not reachable through public controls')
      await select.selectOption(options[selectedIndex].value)
      await expect.poll(ids, { timeout: 30000 }).toEqual(snapshot.items.filter(item => valueFor(item) === selected.value).map(item => item.id).sort())
      await host(toolbar.id).locator('.query-toolbar__reset').click()
      await expect.poll(ids, { timeout: 30000 }).toEqual(allIds)
      sample.checks.push('public-genre-filter/reset/independent-record-identities')
      const geometry = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }))
      if (geometry.document > geometry.viewport + 1 || sample.operations.length || sample.errors.length) throw new Error('Overflow, unexpected mutation or runtime error')
      sample.geometry = geometry; sample.passed = true
    } catch (error) { sample.failure = String(error); sample.passed = false }
    await context.close()
  }
  report.passed = report.samples.every(sample => sample.passed)
} finally { await browser.close(); await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2)) }
console.log(JSON.stringify({ passed: report.passed, samples: report.samples.map(({ layout, failure, checks }) => ({ layout, failure, checks })) }))
if (!report.passed) process.exitCode = 1
