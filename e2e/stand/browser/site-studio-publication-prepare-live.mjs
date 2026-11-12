// Explicitly admitted live preparation acceptance. No provider responses are
// replaced. A publish command is always blocked before it leaves the browser.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
const env = process.env
const APP = env.ADAOS_E2E_SCENARIO_ID, webspace = env.ADAOS_E2E_WEBSPACE_ID
const subnet = env.ADAOS_E2E_SUBNET_ID, token = env.ADAOS_E2E_HUB_TOKEN, output = env.ADAOS_E2E_OUTPUT
const expectedOperation = env.ADAOS_E2E_PREPARED_OPERATION
const expectedRevision = env.ADAOS_E2E_PREPARED_REVISION
const client = env.ADAOS_E2E_CLIENT_URL || 'http://localhost:8100/'
const hub = env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
if (env.ADAOS_E2E_ALLOW_PUBLICATION_PREPARE !== '1' || !APP || !webspace || !subnet || !token || !output || !expectedOperation || !expectedRevision)
  throw new Error('Explicit live preparation admission, scope and retained candidate are required')
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
const report = { checks: [], errors: [], responses: [], publicationWrites: 0 }
page.on('pageerror', error => report.errors.push(error.message))
await page.route('**/api/tools/call', async route => {
  const tool = String(route.request().postDataJSON()?.tool || '')
  if (tool.endsWith('publish_candidate')) {
    report.publicationWrites++
    return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: { error: 'publication_not_authorized_by_acceptance' } }) })
  }
  await route.continue()
})
page.on('response', async response => {
  if (!response.url().includes('/api/tools/call')) return
  const request = response.request().postDataJSON()
  if (!String(request?.tool).endsWith('prepare_publication')) return
  try {
    const value = await response.json()
    report.responses.push({ status: response.status(), result: value.result || value })
  } catch (error) { report.errors.push(String(error)) }
})
await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
  const close = page.locator('.component-updates-panel__tools button').last()
  if (await close.isVisible().catch(() => false)) await close.click()
})
const host = id => page.locator(`[data-webui-widget-id="${id}"]`).last()
try {
  const url = new URL(client)
  for (const [key, value] of Object.entries({ scenario_id: APP, webspace_id: webspace, webspace, try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 90000 })
  await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({ timeout: 120000 })
  const publicationTab = host('authoring_tools').getByRole('button', { name: 'Публикация', exact: true })
  if (await publicationTab.isVisible()) await publicationTab.click()
  else {
    await host('authoring_tools').getByRole('button', { name: /More|Ещё|Еще/ }).click()
    await page.getByText('Публикация', { exact: true }).last().click()
  }
  const commands = host('publication_commands')
  const prepare = commands.getByRole('button', { name: 'Подготовить публикацию', exact: true })
  await expect(commands.getByRole('button', { name: /Опубликовать/ })).toBeEnabled({ timeout: 45000 })
  for (let index = 0; index < 2; index++) {
    await expect(prepare).toBeEnabled()
    await prepare.click()
    await expect.poll(() => report.responses.length, { timeout: 90000 }).toBe(index + 1)
    const response = report.responses[index]
    expect(response.status).toBe(200)
    expect(response.result.item.active_revision_id).toBe(expectedRevision)
    expect(response.result.item.candidate_operation_id).toBe(expectedOperation)
    expect(response.result.item.can_publish).toBe(true)
    await expect(host('publication_feedback').locator('[data-task-status="completed"]')).toBeVisible()
    await expect(host('publication_feedback').locator('[role="alert"]')).toHaveCount(0)
  }
  report.checks.push('Two real preparation clicks return HTTP 200 and the exact retained Root candidate')
  await expect(commands.getByRole('button', { name: /Опубликовать/ })).toBeEnabled()
  report.checks.push('Preparation result is visible and publishing the prepared candidate is available')
  expect(report.publicationWrites).toBe(0)
  report.checks.push('No website activation was requested')
  await page.screenshot({ path: output + '/live-prepared.png', fullPage: true })
  report.passed = report.errors.length === 0
} catch (error) {
  report.passed = false; report.errors.push(String(error))
  await page.screenshot({ path: output + '/live-preparation-failed.png', fullPage: true }).catch(() => {})
} finally {
  await fs.writeFile(output + '/live-preparation-report.json', JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.passed) process.exitCode = 1
