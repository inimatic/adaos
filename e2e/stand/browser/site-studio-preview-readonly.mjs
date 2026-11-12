import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'

const APP = process.env.ADAOS_E2E_SCENARIO_ID
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const token = process.env.ADAOS_E2E_HUB_TOKEN
const output = process.env.ADAOS_E2E_OUTPUT
const client = process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/'
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
if (!APP || !webspace || !subnet || !token || !output) throw new Error('Explicit application, webspace, subnet, token and output are required')
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'ru-RU' })
await context.addInitScript(({ token, hub, webspace, subnet, origin }) => {
  if (location.origin !== origin) return
  window.__ADAOS_DEBUG__ = true; window.__ADAOS_BASE__ = hub; window.__ADAOS_TOKEN__ = token
  for (const [key, value] of Object.entries({ adaos_device_id: 'site-studio-connected-beta', adaos_webspace_id: webspace,
    adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
    adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
}, { token, hub, webspace, subnet, origin: new URL(client).origin })
const page = await context.newPage()
const report = { client, checks: [], errors: [], frameApiRequests: [] }
page.on('pageerror', error => report.errors.push(error.message))
page.on('request', request => {
  try {
    if (request.frame().parentFrame() && /\/(?:api|hubs)\//.test(new URL(request.url()).pathname))
      report.frameApiRequests.push({ path: new URL(request.url()).pathname, method: request.method() })
  } catch {}
})
const host = id => page.locator(`[data-webui-widget-id="${id}"]`).last()
try {
  const url = new URL(client)
  for (const [key, value] of Object.entries({ scenario_id: APP, webspace_id: webspace, webspace, try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 90000 })
  await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({ timeout: 120000 })
  const frame = page.frameLocator('ada-site-preview-widget iframe')
  await expect(frame.locator('ada-public-site-root')).toHaveAttribute('data-public-site-state', 'ready')
  await expect(frame.locator('app-root')).toHaveCount(0)
  report.checks.push('Preview loads the public renderer from the management origin')
  const inspect = host('site_preview').locator('.site-preview__interaction button').nth(1)
  await inspect.click()
  await expect(inspect).toHaveAttribute('aria-pressed', 'true')
  await expect(frame.locator('ada-public-site-widget-host.is-preview-inspect').first()).toBeVisible()
  await frame.locator('ada-public-site-widget-host.is-preview-inspect').first().click()
  await expect(inspect).toHaveAttribute('aria-pressed', 'true')
  await expect(host('site_identity')).toBeVisible()
  await expect(host('authoring_tools')).toBeVisible()
  report.checks.push('Inspection remains selected after state updates and selects a rendered component')
  await host('site_preview').locator('.site-preview__interaction button').first().click()
  await expect(inspect).toHaveAttribute('aria-pressed', 'false')
  await expect(frame.locator('ada-public-site-widget-host.is-preview-inspect')).toHaveCount(0)
  report.checks.push('Navigation can be restored without reloading the iframe')
  await page.waitForTimeout(10000)
  await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible()
  expect(report.frameApiRequests).toEqual([])
  report.checks.push('Preview opens no Hub, YDoc or attachment connections of its own')
  await page.screenshot({ path: output + '/preview.png', fullPage: true })
  report.passed = report.errors.length === 0
} catch (error) {
  report.passed = false; report.errors.push(String(error))
  await page.screenshot({ path: output + '/preview-failed.png', fullPage: true }).catch(() => {})
} finally {
  await fs.writeFile(output + '/preview-report.json', JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.passed) process.exitCode = 1
