import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from './node_modules/playwright/index.mjs'

const api = String(process.env.ADAOS_E2E_API || 'http://127.0.0.1:8777').replace(/\/$/, '')
const client = String(process.env.ADAOS_E2E_CLIENT || 'http://127.0.0.1:8100').replace(/\/$/, '')
const token = String(process.env.ADAOS_E2E_HUB_TOKEN || 'dev-local-token')
const webspace = String(process.env.ADAOS_E2E_WEBSPACE || 'desktop-codex-acceptance')
if (!webspace.startsWith('desktop-codex-') && process.env.ADAOS_E2E_ALLOW_SHARED_WEBSPACE !== '1') {
  throw new Error('Use an isolated acceptance Webspace; shared scenario changes require explicit opt-in')
}
const subnet = String(process.env.ADAOS_E2E_SUBNET || 'sn_6acf0c01')
const repoRoot = fileURLToPath(new URL('../../../', import.meta.url))
const outputRef = process.env.ADAOS_E2E_ARTIFACT_DIR || 'e2e/artifacts/management-chat-viewport'
const output = path.isAbsolute(outputRef) ? outputRef : path.resolve(repoRoot, outputRef)
const viewports = [
  { id: 'phone', width: 390, height: 844 },
  { id: 'compact', width: 800, height: 900 },
  { id: 'wide', width: 1440, height: 900 },
]

await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const report = { startedAt: new Date().toISOString(), webspace, cases: [], errors: [],
  scope: 'Live Management layout with DOM-only long-feed stress; no conversation messages are sent or persisted' }

async function setManagementScenario(request) {
  const response = await request.post(
    `${api}/api/node/yjs/webspaces/${encodeURIComponent(webspace)}/scenario`,
    {
      headers: { 'X-AdaOS-Token': token, 'content-type': 'application/json' },
      data: {
        scenario_id: 'web_desktop',
        set_home: false,
        wait_for_rebuild: true,
        include_runtime: false,
        request_source: 'e2e.management_chat_viewport',
      },
    },
  )
  if (!response.ok()) {
    throw new Error(`set web_desktop: ${response.status()} ${await response.text()}`)
  }
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

for (const viewport of viewports) {
  const context = await browser.newContext({ viewport, serviceWorkers: 'block' })
  await context.addInitScript(({ api, token, webspace, subnet }) => {
    window.__ADAOS_BASE__ = api
    window.__ADAOS_TOKEN__ = token
    window.__ADAOS_DEBUG__ = true
    for (const [key, value] of Object.entries({
      adaos_device_id: `e2e-management-chat-${webspace}`,
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
  const browserErrors = []
  page.on('pageerror', error => browserErrors.push(String(error?.message || error)))
  try {
    await setManagementScenario(context.request)
    await page.goto(desktopUrl(), { waitUntil: 'domcontentloaded', timeout: 90_000 })
    // A fresh test browser may receive an automatic release-review overlay.
    // Closing it is not accepting/publishing the release or changing user prefs.
    const closeUpdates = page.locator('.component-updates-panel button[aria-label="Close"]')
    if (await closeUpdates.isVisible().catch(() => false)) await closeUpdates.click()
    const primaryNavigation = page.locator('[data-webui-widget-id="primary-nav"]')
    await primaryNavigation.waitFor({ state: 'visible', timeout: 90_000 })
    const chatTab = primaryNavigation.locator('[data-command-id="chat"]').first()
    await chatTab.click()
    if (await closeUpdates.isVisible().catch(() => false)) await closeUpdates.click()
    const liveLayout = await page.locator('.desktop-grid').getAttribute('class')
    console.log(JSON.stringify({ viewport: viewport.id, liveLayout }))
    const grid = page.locator('.desktop-grid.viewport-conversation')
    const composer = page.locator('ada-chat-widget .composer')
    const messages = page.locator('ada-chat-widget .messages')
    await grid.waitFor({ state: 'visible', timeout: 15_000 })
    await composer.waitFor({ state: 'visible' })

    const geometry = await page.evaluate(() => {
      const grid = document.querySelector('.desktop-grid.viewport-conversation')
      const composer = document.querySelector('ada-chat-widget .composer')
      const messages = document.querySelector('ada-chat-widget .messages')
      const navigation = document.querySelector('.desktop-grid.viewport-conversation > .area-role-navigation')
      const rect = element => {
        const value = element?.getBoundingClientRect()
        return value ? { top: value.top, bottom: value.bottom, height: value.height } : null
      }
      return {
        viewportHeight: window.innerHeight,
        documentScrollHeight: document.documentElement.scrollHeight,
        grid: rect(grid),
        navigation: rect(navigation),
        messages: rect(messages),
        composer: rect(composer),
        messageOverflowY: messages ? getComputedStyle(messages).overflowY : null,
      }
    })
    const composerVisible = Boolean(
      geometry.composer
      && geometry.composer.top >= 0
      && geometry.composer.bottom <= geometry.viewportHeight + 1,
    )
    const feedOwnsScroll = geometry.messageOverflowY === 'auto' || geometry.messageOverflowY === 'scroll'
    const scrollProof = await page.evaluate(() => {
      const feed = document.querySelector('ada-chat-widget .messages')
      const composer = document.querySelector('ada-chat-widget .composer')
      const controls = document.querySelector('.desktop-grid.viewport-conversation > .area-role-navigation')
      const before = { composer: composer.getBoundingClientRect().top, controls: controls.getBoundingClientRect().top }
      const stress = document.createElement('div')
      stress.dataset.e2eLayoutStress = 'true'
      stress.style.height = '4000px'
      stress.style.flex = '0 0 4000px'
      stress.textContent = 'E2E: synthetic long-feed layout stress (not persisted)'
      feed.append(stress)
      feed.scrollTop = 0
      feed.scrollTop = 600
      const proof = {
        feedScrolled: feed.scrollTop > 0,
        feedScrollHeight: feed.scrollHeight,
        feedClientHeight: feed.clientHeight,
        composerPinned: Math.abs(composer.getBoundingClientRect().top - before.composer) < 1,
        controlsPinned: Math.abs(controls.getBoundingClientRect().top - before.controls) < 1,
        composerBottom: composer.getBoundingClientRect().bottom,
      }
      stress.remove()
      return proof
    })
    const identity = await page.evaluate(() => {
      const desktop = window.ng?.getComponent(document.querySelector('ada-desktop'))
      const schema = desktop?.pageSchema
      return {
        scenarioId: schema?.id || null,
        revision: schema?.revision || schema?.version || null,
        release: schema?._adaos || null,
        materialization: window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization || null,
      }
    })
    report.cases.push({ viewport, composerVisible, feedOwnsScroll, scrollProof, geometry, identity, browserErrors })
    await page.screenshot({
      path: path.join(output, `${viewport.id}.png`),
      animations: 'disabled',
    })
  } catch (error) {
    report.errors.push(`${viewport.id}: ${String(error?.stack || error)}`)
    console.log(JSON.stringify({ viewport: viewport.id, error: String(error?.message || error) }))
    await page.screenshot({
      path: path.join(output, `${viewport.id}-failure.png`),
      animations: 'disabled',
    }).catch(() => {})
  } finally {
    await context.close()
  }
}

report.finishedAt = new Date().toISOString()
await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify(report, null, 2))
await browser.close()

if (
  report.errors.length
  || report.cases.length !== viewports.length
  || report.cases.some(item => !item.composerVisible || !item.feedOwnsScroll
    || !item.scrollProof.feedScrolled || !item.scrollProof.composerPinned || !item.scrollProof.controlsPinned
    || item.browserErrors.length)
) process.exitCode = 1
