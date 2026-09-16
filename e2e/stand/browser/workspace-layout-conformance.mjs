import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from './node_modules/playwright/index.mjs'
import { parse as parseYaml } from '../../../src/adaos/integrations/adaos-client/node_modules/yaml/dist/index.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const repositoryRoot = path.resolve(here, '../../..')
const scenarioRoot = path.join(repositoryRoot, '.adaos', 'workspace', 'scenarios')
const runId = String(process.env.ADAOS_LAYOUT_RUN_ID || new Date().toISOString().replace(/[:.]/g, '-'))
const outputRoot = path.resolve(
  process.env.ADAOS_LAYOUT_OUTPUT || path.join(repositoryRoot, 'e2e', 'artifacts', 'layout-conformance', runId),
)
const clientUrl = String(process.env.ADAOS_CLIENT_URL || 'http://127.0.0.1:8100').replace(/\/$/, '')
const hubUrl = String(process.env.ADAOS_HUB_URL || 'http://127.0.0.1:8777').replace(/\/$/, '')
const subnetId = String(process.env.ADAOS_SUBNET_ID || 'sn_6acf0c01')
const webspaceId = String(process.env.ADAOS_WEBSPACE_ID || 'desktop')
const requestedScenarios = String(process.env.ADAOS_LAYOUT_SCENARIOS || '')
  .split(',')
  .map(value => value.trim())
  .filter(Boolean)
const viewports = [
  { name: 'wide', width: 1440, height: 960 },
  { name: 'compact', width: 390, height: 844 },
]

fs.mkdirSync(outputRoot, { recursive: true })

function workspaceScenarioIds() {
  if (requestedScenarios.length) return requestedScenarios
  return fs.readdirSync(scenarioRoot, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => {
      const jsonFile = path.join(scenarioRoot, entry.name, 'scenario.json')
      const yamlFile = path.join(scenarioRoot, entry.name, 'scenario.yaml')
      const file = fs.existsSync(jsonFile) ? jsonFile : yamlFile
      if (!fs.existsSync(file)) return ''
      const source = fs.readFileSync(file, 'utf8')
      const descriptor = file.endsWith('.json') ? JSON.parse(source) : parseYaml(source)
      return String(descriptor.id || descriptor.scenario_id || entry.name).trim()
    })
    .filter(Boolean)
    .sort((left, right) => left.localeCompare(right))
}

const scenarios = workspaceScenarioIds()
const browser = await chromium.launch({ headless: true })
const results = []

for (const viewport of viewports) {
  const context = await browser.newContext({ viewport })
  await context.addInitScript(({ deviceId, hubUrl: initHubUrl, subnetId: initSubnetId, webspaceId: initWebspaceId }) => {
    window.__ADAOS_DEBUG__ = true
    localStorage.setItem('adaos_device_id', deviceId)
    localStorage.setItem('adaos_webspace_id', initWebspaceId)
    localStorage.setItem('adaos_hub_base', initHubUrl)
    localStorage.setItem('adaos_local_hub_base', initHubUrl)
    localStorage.setItem('adaos_try_local_hub', '1')
    localStorage.setItem('adaos_hub_token', 'dev-local-token')
    localStorage.setItem('adaos_local_subnet_id', initSubnetId)
    localStorage.setItem('adaos_selected_zone', 'lo')
    localStorage.setItem('adaos_last_used_zone', 'lo')
  }, {
    deviceId: `layout-conformance-${runId}-${viewport.name}`,
    hubUrl,
    subnetId,
    webspaceId,
  })

  for (const scenario of scenarios) {
    const page = await context.newPage()
    const pageErrors = []
    const consoleErrors = []
    page.on('pageerror', error => pageErrors.push(String(error?.stack || error?.message || error)))
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    const query = new URLSearchParams({
      intent: 'webspace.open',
      zone: 'lo',
      subnet_id: subnetId,
      webspace_id: webspaceId,
      space_kind: 'workspace',
      expected_scenario_id: scenario,
      try_local_hub: '1',
    })
    const started = Date.now()
    let failure = ''

    try {
      await page.goto(`${clientUrl}/?${query}`, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync?.materialization?.currentScenario === expected
      }, scenario, { timeout: 45_000 })
      await page.locator('ada-layout-region[data-region-role]').first().waitFor({ state: 'attached', timeout: 10_000 })
      await page.waitForFunction(
        () => !document.querySelector('[data-webui-render-state="loading"]'),
        null,
        { timeout: 10_000 },
      )
    } catch (error) {
      failure = String(error?.message || error)
    }

    const evidence = await page.evaluate(() => {
      const regions = [...document.querySelectorAll('ada-layout-region[data-region-role]')].map(node => {
        const box = node.getBoundingClientRect()
        const style = getComputedStyle(node)
        return {
          id: node.getAttribute('data-region-id'),
          role: node.getAttribute('data-region-role'),
          compact: node.getAttribute('data-compact-presentation'),
          display: style.display,
          visibility: style.visibility,
          box: { x: box.x, y: box.y, width: box.width, height: box.height },
        }
      })
      const visible = regions.filter(item => item.display !== 'none' && item.visibility !== 'hidden' && item.box.width > 0 && item.box.height > 0)
      const compactOverlays = new Set(['drawer', 'sheet', 'route', 'overflow'])
      const isCompactOverlay = item => innerWidth <= 720 && compactOverlays.has(item.compact)
      const overlapping = []
      for (let leftIndex = 0; leftIndex < visible.length; leftIndex += 1) {
        for (let rightIndex = leftIndex + 1; rightIndex < visible.length; rightIndex += 1) {
          if (isCompactOverlay(visible[leftIndex]) || isCompactOverlay(visible[rightIndex])) continue
          const left = visible[leftIndex].box
          const right = visible[rightIndex].box
          const width = Math.min(left.x + left.width, right.x + right.width) - Math.max(left.x, right.x)
          const height = Math.min(left.y + left.height, right.y + right.height) - Math.max(left.y, right.y)
          if (width > 2 && height > 2) overlapping.push([visible[leftIndex].id, visible[rightIndex].id, Math.round(width * height)])
        }
      }
      const rendererFailures = [...document.querySelectorAll(
        '[data-webui-render-state="error"], [data-webui-render-state="unsupported"], [data-webui-render-state="loading"]',
      )]
        .map(node => ({
          id: node.getAttribute('data-webui-widget-id'),
          type: node.getAttribute('data-webui-widget-type'),
          state: node.getAttribute('data-webui-render-state'),
          text: node.textContent?.replace(/\s+/g, ' ').trim().slice(0, 500),
        }))
      return {
        currentScenario: window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario || null,
        regions,
        overlapping,
        clipped: visible
          .filter(item => item.box.x < -2 || item.box.x + item.box.width > innerWidth + 2)
          .map(item => item.id),
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 2,
        rendererFailures,
      }
    }).catch(error => ({ evaluationError: String(error) }))

    const result = {
      viewport: viewport.name,
      scenario,
      durationMs: Date.now() - started,
      failure,
      pageErrors,
      consoleErrors: [...new Set(consoleErrors)].slice(0, 20),
      ...evidence,
    }
    result.ok = !result.failure
      && result.currentScenario === scenario
      && result.regions?.length > 0
      && !result.overlapping?.length
      && !result.clipped?.length
      && !result.horizontalOverflow
      && !result.rendererFailures?.length
      && !result.pageErrors.length
    results.push(result)
    process.stderr.write(`${viewport.name} ${scenario} ${result.ok ? 'OK' : 'FAIL'} ${result.durationMs}ms\n`)
    if (!result.ok) {
      await page.screenshot({ path: path.join(outputRoot, `${scenario}-${viewport.name}-failure.png`), fullPage: true })
    }
    await page.close()
  }
  await context.close()
}

await browser.close()
fs.writeFileSync(path.join(outputRoot, 'results.json'), `${JSON.stringify(results, null, 2)}\n`, 'utf8')
const failed = results.filter(result => !result.ok)
process.stdout.write(`${JSON.stringify({ runId, outputRoot, checked: results.length, passed: results.length - failed.length, failed }, null, 2)}\n`)
if (failed.length) process.exitCode = 1
