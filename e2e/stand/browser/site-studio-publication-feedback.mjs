// Connected UI qualification. Publication calls are intercepted before leaving
// the browser; this test cannot prepare or publish a real website.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'

const APP = process.env.ADAOS_E2E_SCENARIO_ID
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const token = process.env.ADAOS_E2E_HUB_TOKEN
const output = process.env.ADAOS_E2E_OUTPUT
const client = process.env.ADAOS_E2E_CLIENT_URL || 'http://localhost:8100/'
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
if (!APP || !webspace || !subnet || !token || !output) throw new Error('Explicit connected test scope is required')
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
const report = { checks: [], errors: [], prepareCalls: [], publishCalls: 0 }
page.on('pageerror', error => report.errors.push(error.message))
await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
  const close = page.locator('.component-updates-panel__tools button').last()
  if (await close.isVisible().catch(() => false)) await close.click()
})
let current = 'before-edit', failNext = false, releasePrepare
const fixture = () => ({ status: 'changes_pending', can_prepare: true, can_publish: false,
  active_revision_id: current, active_source_digest: 'sha256:' + (current === 'before-edit' ? '1' : '2').repeat(64),
  changes_pending: true, availability_text: 'Можно подготовить текущие правки сайта к публикации.',
  public_url: 'https://example.invalid/', environment: 'prod' })
await page.route('**/api/tools/call', async route => {
  const request = route.request().postDataJSON(), tool = String(request?.tool || '')
  const respond = result => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ result }) })
  if (tool.endsWith('query_publication')) return respond({ item: fixture() })
  if (tool.endsWith('publish_candidate')) {
    report.publishCalls++
    return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: { error: 'qualification_blocks_publication' } }) })
  }
  if (tool.endsWith('prepare_publication')) {
    report.prepareCalls.push(request.arguments || request.params)
    if (failNext) return route.fulfill({ status: 409, contentType: 'application/json',
      body: JSON.stringify({ detail: { error: 'trial_execution_failed', message: 'Injected Root transfer failure' } }) })
    await new Promise(resolve => { releasePrepare = resolve })
    return respond({ item: { ...fixture(), status: 'candidate_ready', can_publish: true,
      availability_text: 'Подготовленная версия готова к публикации.', candidate_operation_id: 'qualification-only', candidate_plan_digest: 'sha256:' + '3'.repeat(64) } })
  }
  await route.continue()
})
const host = id => page.locator(`[data-webui-widget-id="${id}"]`).last()
try {
  const url = new URL(client)
  for (const [key, value] of Object.entries({ scenario_id: APP, webspace_id: webspace, webspace, try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 90000 })
  await expect(host('authoring_tools')).toBeVisible({ timeout: 120000 })
  const publicationTab = host('authoring_tools').getByRole('button', { name: 'Публикация', exact: true })
  if (await publicationTab.isVisible()) await publicationTab.click()
  else {
    await host('authoring_tools').getByRole('button', { name: /More|Ещё|Еще/ }).click()
    await page.getByText('Публикация', { exact: true }).last().click()
  }
  const commands = host('publication_commands')
  const prepare = commands.getByRole('button', { name: 'Подготовить публикацию', exact: true })
  await expect(prepare).toBeEnabled({ timeout: 30000 })
  current = 'after-edit'
  await prepare.click()
  const feedback = host('publication_feedback')
  await expect(feedback.locator('[data-task-status="running"]')).toBeVisible()
  await expect(prepare).toBeDisabled()
  await expect.poll(() => report.prepareCalls.length).toBe(1)
  expect(report.prepareCalls[0].expected_revision_id).toBe('after-edit')
  expect(report.prepareCalls[0].expected_source_digest).toBe('sha256:' + '2'.repeat(64))
  report.checks.push('Preparation reads a fresh revision after page hydration and blocks duplicate clicks')
  await expect(feedback.locator('progress')).toBeVisible()
  expect(await feedback.locator('progress').getAttribute('value')).toBeNull()
  releasePrepare()
  await expect(feedback.locator('[data-task-status="completed"]')).toBeVisible()
  await expect(feedback).toContainText('Подготовленная версия готова к публикации.')
  await expect(commands.getByRole('button', { name: /Опубликовать/ })).toBeEnabled()
  report.checks.push('Visible indeterminate activity becomes a persistent preparation result')
  failNext = true
  await prepare.click()
  await expect(feedback.locator('[data-task-status="failed"]')).toBeVisible()
  await expect(feedback.locator('[role="alert"]')).toBeVisible()
  await expect(prepare).toBeDisabled()
  await commands.getByRole('button', { name: 'Обновить', exact: true }).click()
  await expect(prepare).toBeEnabled()
  report.checks.push('A failure stays visible and requires status refresh before another preparation')
  expect(report.publishCalls).toBe(0)
  report.checks.push('Qualification performs no real publication or content mutation')
  await page.screenshot({ path: output + '/publication-feedback.png', fullPage: true })
  report.passed = report.errors.length === 0
} catch (error) {
  report.passed = false; report.errors.push(String(error))
  await page.screenshot({ path: output + '/publication-feedback-failed.png', fullPage: true }).catch(() => {})
} finally {
  releasePrepare?.()
  await fs.writeFile(output + '/publication-feedback-report.json', JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.passed) process.exitCode = 1
