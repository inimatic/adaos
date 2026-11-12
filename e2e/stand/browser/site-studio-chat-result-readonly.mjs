import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'

// Verify retained chat results without resubmitting a model request or editing a site.
const app = process.env.ADAOS_E2E_SCENARIO_ID
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const token = process.env.ADAOS_E2E_HUB_TOKEN
const output = process.env.ADAOS_E2E_OUTPUT
const job = process.env.ADAOS_E2E_CHAT_RESULT_ID
const release = process.env.ADAOS_E2E_EXPECT_RELEASE
const expectedCount = Number(process.env.ADAOS_E2E_EXPECT_RESULT_COUNT || '1')
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const client = process.env.ADAOS_E2E_CLIENT_URL || 'http://localhost:8100/'
if (!app || !webspace || !token || !output || !job || !release || ![1, 2].includes(expectedCount)) {
  throw Error('Explicit Application, Desktop, token, output, retained job and expected release are required')
}
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'ru-RU' })
await context.addInitScript(({ token, hub, webspace, origin }) => {
  if (location.origin !== origin) return
  window.__ADAOS_DEBUG__ = true; window.__ADAOS_BASE__ = hub; window.__ADAOS_TOKEN__ = token
  for (const [key, value] of Object.entries({ adaos_device_id: 'site-studio-chat-result-check', adaos_webspace_id: webspace,
    adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
    adaos_local_subnet_id: 'sn_6acf0c01', adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
}, { token, hub, webspace, origin: new URL(client).origin })
const page = await context.newPage()
const report = { checks: [], errors: [], calls: [], requests: [], responses: [], failedCalls: [], blockedWrites: [] }
page.on('pageerror', error => report.errors.push(error.message))
page.on('request', request => {
  if (!request.url().includes('/api/tools/call')) return
  const tool = String(request.postDataJSON()?.tool || '')
  if (tool.startsWith(app + '_skill') && report.requests.length < 100) report.requests.push(tool)
})
page.on('response', async response => {
  if (!response.url().includes('/api/tools/call')) return
  const data = response.request().postDataJSON()
  if (String(data?.tool).endsWith(':query_discussion')) report.calls.push({ status: response.status(), release: response.headers()['x-adaos-release-digest'] })
  if (String(data?.tool).startsWith(app + '_skill') && report.responses.length < 100) {
    report.responses.push({ tool: data.tool, status: response.status() })
  }
  if (String(data?.tool).startsWith(app + '_skill') && !response.ok()) {
    report.failedCalls.push({ tool: data.tool, status: response.status(), detail: (await response.text()).slice(0,1600) })
  }
})
await page.route('**/api/tools/call', async route => {
  const tool = String(route.request().postDataJSON()?.tool || '')
  if (/[:.](discuss|resume_discussion|cancel_authoring|commit_site_change|undo_site_change|mutate_records|publish_candidate|prepare_publication)$/.test(tool)) {
    report.blockedWrites.push(tool)
    return route.fulfill({ status: 403, body: 'Read-only chat-result verification' })
  }
  return route.continue()
})
await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
  const close = page.locator('.component-updates-panel__tools button').last()
  if (await close.isVisible().catch(() => false)) await close.click()
})
async function call(name, args = {}) {
  const response = await context.request.post(hub + '/api/tools/call', {
    headers: { 'X-AdaOS-Token': token },
    data: { tool: app + '_skill:' + name, arguments: args, dev: false,
      context: { webspace_id: webspace, scenario_id: app, current_scenario_id: app } }, timeout: 65000,
  })
  expect(response.ok()).toBeTruthy()
  expect(response.headers()['x-adaos-release-digest']).toBe(release)
  const body = await response.json()
  return body.result || body
}
const host = id => page.locator(`[data-webui-widget-id="${id}"]`).last()
try {
  const active = (await call('query_active_project')).item
  const discussion = await call('query_discussion', { project_id: active.id, selection_revision: active.revision })
  const response = discussion.messages.find(message => message.id === job + '-response')
  expect(response).toBeTruthy()
  report.terminalRows = discussion.messages.filter(message => message.id === job + '-response'
    || (message.progress_group_id === job && message.progress_phase === 'review')).map(message => ({ id: message.id,
      group: message.progress_group_id, phase: message.progress_phase, status: message.progress_status }))
  expect(report.terminalRows).toHaveLength(expectedCount)
  const url = new URL(client)
  for (const [key, value] of Object.entries({ scenario_id: app, webspace_id: webspace, webspace, try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 90000 })
  await expect(host('authoring_tools')).toBeVisible({ timeout: 120000 })
  const chat = host('authoring_tools').getByRole('button', { name: 'Чат', exact: true })
  if (await chat.isVisible()) await chat.click()
  const matches = host('site_discussion').locator('.msg').filter({ hasText: response.text })
  await expect(matches).toHaveCount(expectedCount, { timeout: 60000 })
  await matches.last().scrollIntoViewIfNeeded()
  await page.screenshot({ path: output + '/chat-result.png' })
  report.checks.push(`Retained result renders ${expectedCount} time(s) in the actual chat`)
  if (expectedCount === 1) {
    await expect(matches.first().getByRole('button', { name: 'Результат', exact: true })).toBeVisible()
    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(matches).toHaveCount(1, { timeout: 90000 })
    report.checks.push('One grouped Result page survives browser reload')
    if (process.env.ADAOS_E2E_CHECK_PUBLICATION === '1') {
      const publication = host('authoring_tools').getByRole('button', { name: 'Публикация', exact: true })
      if (await publication.isVisible()) await publication.click()
      else {
        await host('authoring_tools').locator('button:not([data-command-id]):visible').last().click()
        await page.locator('[data-command-id="publication"]:visible').last().click()
      }
      await host('publication_commands').getByRole('button', { name: 'Обновить', exact: true }).click()
      await expect(host('publication_plan')).toContainText('Текущая редакция сайта опубликована.', { timeout: 60000 })
      await expect(page.getByText('Data is temporarily unavailable.', { exact: true })).toHaveCount(0)
      await expect(page.getByText(/Среда исполнения выбранного Trial недоступна/)).toHaveCount(0)
      await expect(page.getByText('beta.adaos.io', { exact: true }).first()).toBeVisible({ timeout: 60000 })
      report.checks.push('Publication status and connected domain load in the selected Application runtime')
      await page.screenshot({ path: output + '/publication-available.png' })
    }
  }
  expect(report.blockedWrites).toHaveLength(0)
  expect(report.failedCalls).toHaveLength(0)
  expect(report.calls.some(call => call.status === 200 && call.release === release)).toBeTruthy()
  report.passed = report.errors.length === 0
} catch (error) {
  report.passed = false; report.errors.push(String(error))
  await page.screenshot({ path: output + '/failed.png' }).catch(() => {})
} finally {
  await fs.writeFile(output + '/chat-result.json', JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.passed) process.exitCode = 1
