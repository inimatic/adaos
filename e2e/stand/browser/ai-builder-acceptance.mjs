// Read-only browser proof on the explicitly named synthetic acceptance Preview.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const output = process.env.ADAOS_E2E_OUTPUT
const host = process.env.ADAOS_E2E_PREVIEW_HOST
const scenario = process.env.ADAOS_E2E_PREVIEW_SCENARIO
if (!output || !host || !scenario || !process.env.ADAOS_E2E_HUB_TOKEN) {
  throw new Error('Explicit synthetic Preview, output and local owner credential required')
}
const report = { ok: false, providerCalls: 0, errors: [], host, scenario }
const browser = await chromium.launch({ headless: true })
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'en-US' })
  await context.addInitScript(({ token, subnet, hub }) => {
    for (const [key, value] of Object.entries({ adaos_local_hub_url: hub, adaos_local_hub_token: token,
      adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en' })) {
      localStorage.setItem(key, value)
    }
  }, { token: process.env.ADAOS_E2E_HUB_TOKEN, subnet: process.env.ADAOS_E2E_SUBNET_ID,
    hub: process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777' })
  const page = await context.newPage()
  page.on('pageerror', error => report.errors.push(String(error)))
  await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
    await page.locator('.component-updates-panel__tools button').last().dispatchEvent('click')
  })
  const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100')
  for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo',
    subnet_id: process.env.ADAOS_E2E_SUBNET_ID, webspace_id: host,
    expected_scenario_id: scenario, try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  const heading = page.getByText('Core AI accepted', { exact: true }).filter({ visible: true })
  await expect(heading.first()).toBeVisible({ timeout: 60_000 })
  report.changedHeadingVisible = true
  await fs.mkdir(output, { recursive: true })
  await heading.first().screenshot({ path: path.join(output, 'builder-ai-changed-heading.png') })
  report.ok = report.errors.length === 0
} catch (error) {
  report.errors.push(String(error))
} finally {
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, 'builder-ai-preview-result.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.ok) process.exitCode = 1
