import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from './node_modules/playwright/index.mjs'

const api = String(process.env.ADAOS_E2E_API || 'http://127.0.0.1:8777').replace(/\/$/, '')
const client = String(process.env.ADAOS_E2E_CLIENT || 'http://127.0.0.1:8100').replace(/\/$/, '')
const token = String(process.env.ADAOS_E2E_HUB_TOKEN || 'dev-local-token')
const webspace = String(process.env.ADAOS_E2E_WEBSPACE || 'desktop')
const subnet = String(process.env.ADAOS_E2E_SUBNET || 'sn_6acf0c01')
const repoRoot = fileURLToPath(new URL('../../../', import.meta.url))
const outputRef = process.env.ADAOS_E2E_ARTIFACT_DIR || 'e2e/artifacts/scenario-transition-regression'
const output = path.isAbsolute(outputRef) ? outputRef : path.resolve(repoRoot, outputRef)
const targets = [
  { label: 'Applications', scenario: 'applications' },
  { label: 'Users & Access', scenario: 'users_access' },
]

await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const report = { startedAt: new Date().toISOString(), cases: [], errors: [] }

async function setScenario(request, scenario, source) {
  const response = await request.post(`${api}/api/node/yjs/webspaces/${encodeURIComponent(webspace)}/scenario`, {
    headers: { 'X-AdaOS-Token': token, 'content-type': 'application/json' },
    data: {
      scenario_id: scenario,
      set_home: false,
      wait_for_rebuild: true,
      include_runtime: false,
      request_source: source,
    },
  })
  if (!response.ok()) throw new Error(`set ${scenario}: ${response.status()} ${await response.text()}`)
}

function desktopUrl() {
  const url = new URL(client)
  for (const [key, value] of Object.entries({
    intent: 'webspace.open',
    zone: 'lo',
    subnet_id: subnet,
    webspace_id: webspace,
    space_kind: 'workspace',
    expected_scenario_id: 'web_desktop',
    try_local_hub: '1',
    runtime_debug: '1',
  })) url.searchParams.set(key, value)
  return url.href
}

for (const target of targets) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ api, token, webspace, subnet }) => {
    window.__ADAOS_BASE__ = api
    window.__ADAOS_TOKEN__ = token
    for (const [key, value] of Object.entries({
      adaos_device_id: 'e2e-scenario-transition-regression',
      adaos_webspace_id: webspace,
      adaos_hub_base: api,
      adaos_local_hub_base: api,
      adaos_try_local_hub: '1',
      adaos_hub_token: token,
      adaos_local_subnet_id: subnet,
      adaos_selected_zone: 'lo',
      adaos_last_used_zone: 'lo',
      adaos_lang: 'en',
      'adaos.runtime_debug': '1',
      'adaos.runtime_debug.opt_in.v1': '1',
    })) localStorage.setItem(key, value)
  }, { api, token, webspace, subnet })
  const page = await context.newPage()
  page.setDefaultTimeout(90_000)
  const errors = []
  page.on('pageerror', error => errors.push(String(error?.message || error)))
  try {
    await setScenario(context.request, 'web_desktop', 'e2e.scenario_transition.prepare')
    await page.goto(desktopUrl(), { waitUntil: 'domcontentloaded', timeout: 90_000 })
    await page.waitForFunction(() => {
      const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
      return sync?.providerSynced === true
        && sync?.materializationReady === true
        && sync?.materialization?.currentScenario === 'web_desktop'
    }, undefined, { timeout: 90_000 })
    const tile = page
      .locator('[data-webui-widget-id="desktop-icons"]')
      .getByText(target.label, { exact: true })
      .first()
    await tile.waitFor({ state: 'visible', timeout: 30_000 })
    await page.evaluate(() => window.__ADAOS_RUNTIME_DEBUG__?.clear?.())

    const samples = []
    const started = Date.now()
    let firstTargetAt = null
    let clickSettled = false
    let clickError = null
    const click = tile.click().then(() => { clickSettled = true }).catch(error => {
      clickSettled = true
      clickError = String(error?.message || error)
    })
    const deadline = Date.now() + 25_000
    while (Date.now() < deadline) {
      const sample = await page.evaluate(() => {
        const debug = window.__ADAOS_DEBUG_STATE__?.()
        const appRoot = document.querySelector('app-root')
        const app = appRoot && window.ng?.getComponent?.(appRoot)
        return {
          materializedScenario: debug?.sync?.materialization?.currentScenario || null,
          materializationReady: debug?.sync?.materializationReady === true,
          providerSynced: debug?.sync?.providerSynced === true,
          angularScenario: app?.currentScenario || null,
        }
      })
      const previous = samples.at(-1)?.value
      if (!previous || JSON.stringify(previous) !== JSON.stringify(sample)) {
        samples.push({ atMs: Date.now() - started, value: sample })
      }
      if (
        (sample.materializedScenario === target.scenario || sample.angularScenario === target.scenario)
        && firstTargetAt === null
      ) firstTargetAt = Date.now()
      if (
        firstTargetAt !== null
        && Date.now() - firstTargetAt >= 3500
        && sample.materializedScenario === target.scenario
        && sample.angularScenario === target.scenario
        && sample.materializationReady
      ) break
      await page.waitForTimeout(40)
    }
    await click
    const firstTargetIndex = samples.findIndex(item =>
      item.value.materializedScenario === target.scenario || item.value.angularScenario === target.scenario)
    const postTarget = firstTargetIndex < 0 ? [] : samples.slice(firstTargetIndex + 1)
    const rebound = postTarget.some(item =>
      item.value.materializedScenario === 'web_desktop' || item.value.angularScenario === 'web_desktop')
    report.cases.push({
      target,
      clickSettled,
      clickError,
      firstTargetIndex,
      rebound,
      samples,
      errors,
      runtimeEvents: await page.evaluate(() => window.__ADAOS_RUNTIME_DEBUG__?.get?.() || []),
    })
    await page.screenshot({
      path: path.join(output, `${target.scenario}.png`),
      fullPage: true,
      animations: 'disabled',
    })
  } catch (error) {
    report.errors.push(`${target.scenario}: ${String(error?.stack || error)}`)
  } finally {
    await context.close()
  }
}

try {
  const restoreContext = await browser.newContext()
  await setScenario(restoreContext.request, 'web_desktop', 'e2e.scenario_transition.restore')
  await restoreContext.close()
} catch (error) {
  report.errors.push(`restore web_desktop: ${String(error?.stack || error)}`)
}

report.finishedAt = new Date().toISOString()
await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify({
  cases: report.cases.map(item => ({
    target: item.target,
    rebound: item.rebound,
    clickError: item.clickError,
    samples: item.samples,
  })),
  errors: report.errors,
}, null, 2))
await browser.close()
if (
  report.errors.length
  || report.cases.length !== targets.length
  || report.cases.some(item => item.rebound || item.clickError || item.firstTargetIndex < 0 || item.errors.length)
) process.exitCode = 1
