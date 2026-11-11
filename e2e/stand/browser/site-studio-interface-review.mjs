import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const token = process.env.ADAOS_E2E_HUB_TOKEN
if (!token) throw new Error('An explicit local Hub token is required')
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'e2e/artifacts/site-studio-interface-20261111')
await fs.mkdir(output, { recursive: true })
const client = process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8103/'
const url = new URL(client)
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: 'sn_6acf0c01',
  webspace_id: 'dev2-dev', space_kind: 'development', expected_scenario_id: 'site_studio_workbench_20261111', try_local_hub: '1' })) url.searchParams.set(key, value)
const report = { scope: 'Native authored-WebUI prototype; no real LLM or publication', checks: [], errors: [], writes: [] }
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'ru-RU' })
await context.addInitScript(({ token }) => {
  window.__ADAOS_DEBUG__ = true
  window.__ADAOS_BASE__ = 'http://127.0.0.1:8777'
  window.__ADAOS_TOKEN__ = token
  for (const [key, value] of Object.entries({ adaos_device_id: 'site-studio-interface-review', adaos_webspace_id: 'dev2-dev',
    adaos_hub_base: 'http://127.0.0.1:8777', adaos_local_hub_base: 'http://127.0.0.1:8777', adaos_try_local_hub: '1',
    adaos_hub_token: token, adaos_local_subnet_id: 'sn_6acf0c01', adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo',
    adaos_lang: 'ru', adaos_env_type: 'dev' })) localStorage.setItem(key, value)
}, { token })
const page = await context.newPage()
page.setDefaultTimeout(20000)
await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
  const close = page.locator('.component-updates-panel__tools button').last()
  if (await close.isVisible().catch(() => false)) await close.click()
})
page.on('pageerror', error => report.errors.push(error.message))
page.on('request', request => {
  if (!request.url().includes('/api/tools/call')) return
  const body = request.postDataJSON()
  if (/site_studio_test_.*_skill[:.](mutate|commit|propose|publish|prepare|select|create|set_)/.test(body?.tool || '')) report.writes.push(body.tool)
})
const host = id => page.locator(`[data-webui-widget-id="${id}"]`).last()
const record = name => report.checks.push({ name, passed: true })
async function tool(id) {
  const button = host('authoring_tools').locator(`[data-command-id="${id}"]:visible`).first()
  if (!await button.count()) {
    await host('authoring_tools').locator('button:not([data-command-id]):visible').last().click()
    await page.locator(`[data-command-id="${id}"]:visible`).last().click()
  } else await button.click()
}
async function screenshot(name) { await page.screenshot({ path: path.join(output, name), fullPage: true }) }
try {
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 90000 })
  await host('site_preview').waitFor({ state: 'visible', timeout: 90000 })
  await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({ timeout: 90000 })
  const frame = page.frames().find(frame => frame.url().includes('/site-development/adaos/'))
  if (!frame) throw new Error('Isolated AdaOS preview frame is missing')
  await frame.evaluate(() => window.__studioFrameRetained = 'review')
  await expect(host('site_preview').getByRole('button', { name: 'Выбор', exact: true })).toBeVisible()
  await expect(host('site_discussion').locator('.progress-page')).toHaveCount(3)
  record('native-two-pane-preview-and-shared-phase-card')
  await screenshot('prototype-wide.png')

  const composer = host('site_discussion').locator('textarea').last()
  await composer.fill('Черновик сообщения для выбранного раздела')
  await tool('structure')
  await expect(host('site_structure')).toBeVisible()
  await tool('chat')
  await expect(composer).toHaveValue('Черновик сообщения для выбранного раздела')
  expect(await frame.evaluate(() => window.__studioFrameRetained)).toBe('review')
  record('tool-switch-preserves-chat-draft-and-preview-window')

  await host('site_preview').getByRole('button', { name: 'Выбор', exact: true }).click()
  await frame.locator('[data-webui-widget-id="hero"] h1').click()
  await expect(host('edit-hero')).toBeVisible()
  const heading = host('edit-hero').locator('[data-webui-field-id="heading"] input')
  await heading.fill('Локальный заголовок прототипа')
  await screenshot('prototype-properties.png')
  await tool('chat')
  await frame.locator('[data-webui-widget-id="hero"] h1').click()
  await expect(heading).toHaveValue('Локальный заголовок прототипа')
  record('preview-selection-opens-properties-and-preserves-local-form')

  await tool('changes')
  await host('change_review').locator('[data-command-id="after"]').click()
  await expect(frame.locator('.hero-statement')).toHaveText('Your workspace. Your software. Your rules.', { timeout: 30000 })
  await host('change_review').locator('[data-command-id="undo"]').click()
  await expect(frame.locator('.hero-statement')).not.toHaveText('Your workspace. Your software. Your rules.', { timeout: 30000 })
  expect(await frame.evaluate(() => window.__studioFrameRetained)).toBe('review')
  record('compiled-candidate-comparison-and-local-undo-retain-frame')

  await tool('publication')
  await expect(host('publication_status')).toBeVisible()
  await expect(host('publication_actions').getByRole('button', { name: 'Подготовить публикацию', exact: true })).toBeDisabled()
  await screenshot('prototype-publication.png')
  record('publication-discloses-prototype-and-cannot-write')

  await tool('chat')
  await page.waitForTimeout(2000)
  expect(await frame.evaluate(() => window.__studioFrameRetained)).toBe('review')
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
  record('idle-and-wide-layout-retain-preview-without-document-overflow')

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('.panel-switcher')).toBeVisible()
  await screenshot('prototype-mobile-preview.png')
  await page.locator('.panel-switcher').getByRole('button', { name: 'Редактор', exact: true }).click()
  await expect(host('site_discussion')).toBeVisible()
  await expect(composer).toHaveValue('Черновик сообщения для выбранного раздела')
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
  await screenshot('prototype-mobile-editor.png')
  record('compact-editor-preview-switch-preserves-draft-without-overflow')
  expect(report.writes).toEqual([])
  expect(report.errors).toEqual([])
  report.passed = true
} catch (error) {
  report.passed = false
  report.failure = error.message
  report.visibleText = (await page.locator('body').innerText()).slice(0,6000)
  await screenshot('prototype-failure.png').catch(() => {})
  process.exitCode = 1
} finally {
  await fs.writeFile(path.join(output, 'browser-review.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report))
  await browser.close()
}
