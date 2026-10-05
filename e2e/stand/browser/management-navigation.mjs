import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from './node_modules/playwright/index.mjs'

const api = process.env.ADAOS_E2E_API || 'http://127.0.0.1:8778'
const client = process.env.ADAOS_E2E_CLIENT || 'http://127.0.0.1:8100'
const token = process.env.ADAOS_E2E_HUB_TOKEN
const webspace = process.env.ADAOS_E2E_WEBSPACE || 'desktop-codex-navigation'
if (!token || !webspace.startsWith('desktop-codex-')) throw new Error('Explicit token and isolated test Webspace required')
const output = path.resolve(process.env.ADAOS_E2E_ARTIFACT_DIR || 'e2e/artifacts/management-navigation')
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, serviceWorkers: 'block' })
await context.addInitScript(({ api, token, webspace }) => {
  window.__ADAOS_BASE__ = api
  window.__ADAOS_TOKEN__ = token
  window.__ADAOS_DEBUG__ = true
  for (const [key, value] of Object.entries({
    adaos_device_id: `e2e-navigation-${webspace}`, adaos_webspace_id: webspace,
    adaos_hub_base: api, adaos_local_hub_base: api, adaos_try_local_hub: '1', adaos_hub_token: token,
    adaos_local_subnet_id: 'sn_6acf0c01', adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo',
    adaos_lang: 'en', 'adaos.runtime_debug': '1', 'adaos.runtime_debug.opt_in.v1': '1',
  })) localStorage.setItem(key, value)
}, { api, token, webspace })
const page = await context.newPage()
page.setDefaultTimeout(30_000)
const report = { startedAt: new Date().toISOString(), webspace, cases: [], errors: [], pageErrors: [] }
page.on('pageerror', error => report.pageErrors.push(String(error.message).slice(0, 500)))
report.navigationWarnings = []
page.on('console', msg => {
  // Do not retain console payloads/URLs/credentials; only known route errors.
  const text = msg.text()
  if (text.includes('browser history scenario switch failed')) {
    report.navigationWarnings.push({ at: new Date().toISOString(), kind: 'history_switch_failed',
      projectionTimeout: text.includes('scenario_history_projection_timeout') })
  }
})
// New release notices may arrive after a navigation paint. Closing their panel
// is not a permission decision, acceptance, or persistent notification dismissal.
report.noticePanelsClosed = 0
await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
  await page.locator('.component-updates-panel__tools button[aria-label="Close"]').click()
  report.noticePanelsClosed += 1
})

async function boundedPath(ref) {
  const response = await context.request.get(`${api}/api/node/yjs/webspaces/${webspace}/path`, {
    headers: { 'X-AdaOS-Token': token }, params: { path: ref, max_bytes: 16000 }, timeout: 30_000,
  })
  if (!response.ok()) throw new Error(`read ${ref}: ${response.status()}`)
  const body = await response.json()
  if (!body.available) throw new Error(`missing ${ref}: ${body.reason}`)
  return body.value
}
async function dismissNotices() {
  const close = page.locator('.component-updates-panel__tools button[aria-label="Close"]')
  if (await close.isVisible()) await close.click()
}
async function renderedScenario() {
  return page.evaluate(() => window.ng?.getComponent(document.querySelector('ada-desktop'))?.pageSchema?._adaos?.component?.id || null)
}
async function waitScenario(id) {
  await page.waitForFunction(id => window.ng?.getComponent(document.querySelector('ada-desktop'))?.pageSchema?._adaos?.component?.id === id,
    id, { timeout: 60_000 })
}
async function receipt(action, id, started) {
  const paintMs = Date.now() - started
  // Paint may precede the transaction's history commit. Assert convergence at
  // its boundary, not atomic DOM/history mutation inside one JS callback.
  await page.waitForFunction(id => {
    const query = new URL(window.location.href).searchParams
    return (query.get('scenario_id') || query.get('expected_scenario_id')) === id
  }, id, { timeout: 60_000 })
  const locationCommitMs = Date.now() - started
  const actual = await renderedScenario()
  const authoritative = await boundedPath('ui/current_scenario')
  const query = new URL(page.url()).searchParams
  const locationScenario = query.get('scenario_id') || query.get('expected_scenario_id')
  const value = { action, expected: id, rendered: actual, authoritative, locationScenario, paintMs, locationCommitMs }
  report.cases.push(value)
  console.log(JSON.stringify(value))
  if (actual !== id || authoritative !== id || (locationScenario && locationScenario !== id)) {
    throw new Error(`Navigation did not converge: ${JSON.stringify(value)}`)
  }
}

try {
  const response = await context.request.post(`${api}/api/node/yjs/webspaces/${webspace}/scenario`, {
    headers: { 'X-AdaOS-Token': token },
    data: { scenario_id: 'web_desktop', set_home: true, wait_for_rebuild: true, include_runtime: false,
      request_source: 'e2e.management_navigation' }, timeout: 90_000,
  })
  if (!response.ok()) throw new Error(`initialize isolated scenario: ${response.status()}`)
  const initial = Date.now()
  await page.goto(`${client}/?zone=lo&try_local_hub=1&webspace=${webspace}&webspace_id=${webspace}&scenario_id=web_desktop&view=home`,
    { waitUntil: 'domcontentloaded', timeout: 90_000 })
  await waitScenario('web_desktop')
  await dismissNotices()
  await receipt('initial', 'web_desktop', initial)
  report.ownerTabs = await page.locator('[data-webui-widget-id="primary-nav"] [data-command-id]').evaluateAll(nodes => nodes.map(n => n.dataset.commandId))
  report.avatar = await page.locator('img.user-settings-avatar-image').evaluateAll(nodes => nodes.map(n => ({
    loaded: n.complete && n.naturalWidth > 0, intrinsic: n.naturalWidth, rendered: n.getBoundingClientRect().width,
  })))
  report.launchers = await page.locator('[data-webui-widget-id="desktop-icons"] [data-collection-item-id]').evaluateAll(nodes =>
    nodes.map(n => ({ id: n.dataset.collectionItemId, title: n.textContent.trim().slice(0, 100), disabled: n.getAttribute('aria-disabled') })))
  const labels = process.env.ADAOS_E2E_NAVIGATION_LABELS
    ? JSON.parse(process.env.ADAOS_E2E_NAVIGATION_LABELS)
    : ['Media Center', 'Semantic UI Demo', 'Inbox Triage', 'AdaOS Builder']
  if (!Array.isArray(labels) || !labels.length || labels.some(value => typeof value !== 'string')) {
    throw new Error('ADAOS_E2E_NAVIGATION_LABELS must be a nonempty JSON array of launcher labels')
  }
  report.requestedLabels = labels
  for (const label of labels) {
    await dismissNotices()
    const card = page.locator('[data-webui-widget-id="desktop-icons"] [data-collection-item-id]').filter({ hasText: label }).first()
    await card.waitFor({ state: 'visible' })
    const id = await card.getAttribute('data-collection-item-id')
    if (!id?.startsWith('scenario:')) throw new Error(`Unspecified scenario mapping: ${id}`)
    const scenario = id.slice('scenario:'.length)
    const started = Date.now()
    await card.click()
    await waitScenario(scenario)
    await receipt(`launch:${label}:${id}`, scenario, started)
    const banners = await page.getByText('The selected Trial runtime is not available.', { exact: false }).count()
    if (banners) throw new Error(`Trial-unavailable banner in ${scenario}`)
    const backStarted = Date.now()
    await page.goBack({ waitUntil: 'domcontentloaded' })
    await waitScenario('web_desktop')
    await receipt(`back:${label}`, 'web_desktop', backStarted)
  }
} catch (error) {
  report.errors.push(String(error?.stack || error).slice(0, 2000))
  report.failedLocation = {
    scenario: new URL(page.url()).searchParams.get('scenario_id'),
    view: new URL(page.url()).searchParams.get('view'),
    rendered: await renderedScenario().catch(() => null),
    authoritative: await boundedPath('ui/current_scenario').catch(() => null),
  }
  report.renderSnapshot = await page.evaluate(() => {
    const app = window.ng?.getComponent(document.querySelector('[ng-version]'))
    const snapshot = app?.ydoc?.getRenderMaterializationSnapshot?.() || {}
    return { currentScenario: snapshot.currentScenario, ready: snapshot.ready,
      expectedScenario: snapshot.expectedScenario, missingBranches: snapshot.missingBranches }
  }).catch(() => null)
  report.bodyExcerpt = (await page.locator('body').innerText().catch(() => '')).slice(0, 1800)
  await page.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {})
} finally {
  report.finishedAt = new Date().toISOString()
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ artifact: path.join(output, 'report.json'), cases: report.cases,
    errors: report.errors, pageErrors: report.pageErrors, failedLocation: report.failedLocation }))
  await context.close()
  await browser.close()
}
if (report.errors.length || report.pageErrors.length) process.exitCode = 1
