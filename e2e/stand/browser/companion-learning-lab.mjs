import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from './node_modules/playwright/index.mjs'

const api = process.env.ADAOS_E2E_API || 'http://127.0.0.1:8788'
const client = process.env.ADAOS_E2E_CLIENT || 'http://127.0.0.1:8100'
const token = process.env.ADAOS_E2E_HUB_TOKEN || 'dev-local-token'
const sourceWebspace = process.env.ADAOS_E2E_WEBSPACE || 'desktop-codex-learning'
let webspace = sourceWebspace
if (!webspace.startsWith('desktop-codex-')) throw new Error('Use an isolated qualification Webspace')
const output = path.resolve('e2e/artifacts/companion-learning-lab')
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const report = { api, webspace, cases: [], mutations: 'scenario switch in isolated qualification Webspace only' }
let currentPage
try {
  const request = await browser.newContext()
  await request.request.post(`${api}/api/node/yjs/webspaces/${sourceWebspace}/scenario`, {
    headers: { 'X-AdaOS-Token': token }, data: { scenario_id: 'web_desktop', set_home: false, wait_for_rebuild: true, include_runtime: false }, timeout: 90000,
  })
  const bindingResponse = await request.request.post(`${api}/api/builder/workbench/active-draft`, {
    headers: { 'X-AdaOS-Token': token }, data: { webspace_id: sourceWebspace,
      draft_id: 'draft.companion_console.20261004213149910877', runtime_scenario_id: 'companion_console' }, timeout: 90000,
  })
  const binding = await bindingResponse.json()
  if (!bindingResponse.ok() || !binding.binding?.dev_webspace_id) throw new Error(`Preview binding failed: ${JSON.stringify(binding)}`)
  webspace = binding.binding.dev_webspace_id
  report.previewWebspace = webspace
  await request.close()
  for (const viewport of [{ width: 390, height: 844 }, { width: 1440, height: 1000 }]) {
    const context = await browser.newContext({ viewport, serviceWorkers: 'block' })
    await context.addInitScript(({ api, token, webspace }) => {
      window.__ADAOS_BASE__ = api
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'cdl-qualification', adaos_webspace_id: webspace,
        adaos_hub_base: api, adaos_local_hub_base: api, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: 'sn_6acf0c01', adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
    }, { api, token, webspace })
    const response = await context.request.post(`${api}/api/node/yjs/webspaces/${webspace}/builder-materialize`, {
      headers: { 'X-AdaOS-Token': token }, data: { scenario_id: 'companion_console', revision: '003',
        preview_stage: 'prototype', source_webspace_id: sourceWebspace,
        draft_id: 'draft.companion_console.20261004213149910877' }, timeout: 90000,
    })
    if (!response.ok()) throw new Error(`Materialization ${response.status()}: ${await response.text()}`)
    const materialization = await response.json()
    if (!materialization.ok) throw new Error(`Materialization rejected: ${JSON.stringify(materialization)}`)
    const page = await context.newPage()
    currentPage = page
    const errors = []
    const dataErrors = []
    const foreignApiOrigins = new Set()
    page.on('pageerror', error => errors.push(String(error)))
    page.on('response', async response => {
      if (new URL(response.url()).pathname.startsWith('/api/tools/') && new URL(response.url()).origin !== api) foreignApiOrigins.add(new URL(response.url()).origin)
      if (response.status() >= 400 && response.url().includes('/api/')) dataErrors.push({ status: response.status(), origin: new URL(response.url()).origin, path: new URL(response.url()).pathname,
        body: (await response.text().catch(() => '')).slice(0, 1500).split(token).join('[redacted]') })
    })
    await page.goto(`${client}/?intent=webspace.open&zone=lo&subnet_id=sn_6acf0c01&webspace_id=${webspace}&space_kind=development&expected_scenario_id=companion_console&try_local_hub=1`, { waitUntil: 'domcontentloaded', timeout: 90000 })
    const session = page.locator('[data-webui-widget-id="learning-session"]')
    await session.waitFor({ state: 'visible', timeout: 90000 })
    const feedback = page.locator('[data-webui-widget-id="learning-feedback"]')
    await feedback.waitFor({ state: 'attached' })
    await page.waitForFunction(() => {
      const element = document.querySelector('[data-webui-widget-id="learning-session"]')
      return element && element.textContent.trim().length > 0
    }, undefined, { timeout: 45000 })
    await page.waitForFunction(() => {
      const element = document.querySelector('[data-webui-widget-id="learning-turns"]')
      return element && !element.textContent.includes('Loading data')
    }, undefined, { timeout: 45000 })
    await page.screenshot({ path: path.join(output, `${viewport.width}.png`), fullPage: true })
    const geometry = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }))
    report.cases.push({ viewport, geometry, sessionText: await session.innerText(), feedbackText: await feedback.innerText(), errors, dataErrors,
      foreignApiOrigins: [...foreignApiOrigins] })
    if (foreignApiOrigins.size) throw new Error(`Runtime selection mismatch: expected ${api}; browser used ${[...foreignApiOrigins].join(', ')}`)
    if (geometry.content > geometry.width + 2 || errors.length || dataErrors.length) throw new Error(`Layout, data admission or browser errors at ${viewport.width}`)
    await context.close()
  }
} catch (error) {
  report.error = String(error).split(token).join('[redacted]')
  if (currentPage && !currentPage.isClosed()) {
    await currentPage.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {})
    report.visibleText = await currentPage.locator('body').innerText().catch(() => '')
    report.widgetIds = await currentPage.locator('[data-webui-widget-id]').evaluateAll(nodes => nodes.map(n => n.getAttribute('data-webui-widget-id'))).catch(() => [])
  }
  process.exitCode = 1
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report))
}
