import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal } from './prototype-navigation.mjs'

const expect = baseExpect.configure({ timeout: 30_000 })
const scenario = process.env.ADAOS_E2E_SCENARIO_ID
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const token = process.env.ADAOS_E2E_HUB_TOKEN
const hub = 'http://127.0.0.1:8777'
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT)
if (process.env.ENV_TYPE !== 'dev' || !scenario?.startsWith('test_semantic_') || !token || !webspace || !subnet) throw new Error('Owned DEV semantic fixture required')
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const report = { ok: false, scenario, samples: [], reloads: 0 }
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ hub, token, subnet, webspace }) => {
    window.__ADAOS_DEBUG__ = true
    window.__ADAOS_BASE__ = hub
    window.__ADAOS_TOKEN__ = token
    window.__ADAOS_E2E_DOCUMENT_ID__ = crypto.randomUUID()
    for (const [key, value] of Object.entries({ adaos_device_id: `e2e-warm:${webspace}`, adaos_webspace_id: webspace,
      adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
      adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en' })) localStorage.setItem(key, value)
  }, { hub, token, subnet, webspace })
  const page = await context.newPage()
  let navigations = 0
  page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations++ })
  await installSystemOverlayDismissal(page, () => {})
  const url = new URL('http://127.0.0.1:8100/')
  for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet, webspace_id: webspace,
    space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  const host = page.locator('[data-webui-widget-id="records-list"]').first()
  await expect(host).toContainText('Work queue')
  await expect(host).toContainText('First task')
  const documentIdentity = await page.evaluate(() => window.__ADAOS_E2E_DOCUMENT_ID__)
  await fs.writeFile(path.join(output, 'armed.json'), JSON.stringify({ scenario, ready: true }))
  for (let phase = 1; phase <= 2; phase++) {
    const input = path.join(output, `phase-${phase}.json`)
    const deadline = Date.now() + 90_000
    while (Date.now() < deadline) {
      try { await fs.access(input); break } catch { await new Promise(resolve => setTimeout(resolve, 100)) }
    }
    const command = JSON.parse(await fs.readFile(input, 'utf8'))
    const sample = { phase, expected: command.text, ok: false }
    report.samples.push(sample)
    try {
      await expect(host).toContainText(command.text)
      await expect(host).toContainText('Task subject')
      await expect(host).toContainText('First task')
      sample.sameDocument = await page.evaluate(expected => window.__ADAOS_E2E_DOCUMENT_ID__ === expected, documentIdentity)
      if (!sample.sameDocument) throw new Error('Client document was reloaded')
      sample.ok = true
    } catch (error) {
      sample.error = String(error)
      await fs.writeFile(path.join(output, `phase-${phase}-body.txt`), await page.locator('body').innerText())
    }
    await page.screenshot({ path: path.join(output, `phase-${phase}.png`), fullPage: true })
    await fs.writeFile(path.join(output, `phase-${phase}-result.json`), JSON.stringify(sample, null, 2))
  }
  report.reloads = report.samples.filter(sample => sample.sameDocument === false).length
  report.navigationEvents = navigations
  report.ok = report.samples.every(sample => sample.ok) && report.reloads === 0
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
}
if (!report.ok) process.exitCode = 1
