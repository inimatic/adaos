// Read only the normal model choices in the owned synthetic Builder host.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const output = process.env.ADAOS_E2E_OUTPUT
if (!output || !process.env.ADAOS_E2E_HUB_TOKEN) throw new Error('Local owner session required')
const browser = await chromium.launch({ headless: true })
const report = { ok: false, providerCalls: 0, errors: [], choiceReads: [] }
let inspectedPage
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ token, subnet }) => {
    for (const [key, value] of Object.entries({ adaos_local_hub_url: 'http://127.0.0.1:8777',
      adaos_local_hub_token: token, adaos_local_subnet_id: subnet,
      adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
  }, { token: process.env.ADAOS_E2E_HUB_TOKEN, subnet: process.env.ADAOS_E2E_SUBNET_ID })
  const page = await context.newPage()
  inspectedPage = page
  page.on('response', async response => {
    if (!response.request().postData()?.includes('get_prototype_model_choices')) return
    try {
      const value = await response.json()
      const result = value.result ?? value
      report.choiceReads.push({ status: response.status(), error: value.error || value.detail?.code,
        collectionSize: Array.isArray(result) ? result.length : result?.items?.length })
    } catch { /* A cancelled read has no response body to retain. */ }
  })
  page.on('pageerror', error => report.errors.push(String(error)))
  await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
    await page.locator('.component-updates-panel__tools button').last().dispatchEvent('click')
  })
  const url = new URL('http://127.0.0.1:8100')
  for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo',
    subnet_id: process.env.ADAOS_E2E_SUBNET_ID, webspace_id: 'ai-core-builder-generation-scoped',
    expected_scenario_id: 'builder', try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  await page.waitForFunction(() => !!document.querySelector('ada-chat-widget'), { timeout: 60_000 })
  // Fixture selection uses the public UI state action, not a model/provider call.
  await page.evaluate(async () => {
    const chat = window.ng?.getComponent(document.querySelector('ada-chat-widget'))
    if (!chat?.actions?.handle) throw new Error('Public UI action handler unavailable')
    await chat.actions.handle({ on: 'click', type: 'updateState', params: {
      selectedProjectKind: 'project', selectedProjectId: 'ai_core_full_prototype_scoped',
      workbenchView: 'conversation', conversationMode: 'task' } })
  })
  await page.locator('[data-webui-widget-id="design-workbench-header"]')
    .filter({ visible: true }).last().locator('[data-command-id="settings"]').click()
  const settings = page.locator('[data-webui-widget-id="chat-side-settings"]').filter({ visible: true }).last()
  await expect(settings).toBeVisible({ timeout: 30_000 })
  const picker = settings.locator('[data-webui-field-id="llmModel"] select')
  await expect(picker).toBeVisible({ timeout: 10_000 })
  await expect(picker).toBeEnabled({ timeout: 30_000 })
  const options = await picker.locator('option').evaluateAll(items => items.map(item => ({
    id: item.value.replace(/^\d+:\s*/, ''), value: item.value, label: item.textContent })))
  report.personalOptions = options.filter(item => item.id.startsWith('chatgpt/'))
  report.distinctRootOptions = options.filter(item => !item.id.startsWith('chatgpt/')).length
  if (!report.personalOptions.length || !report.personalOptions.every(item => /personal plan/.test(item.label))) {
    throw new Error('Personal plan disclosure is missing from normal model choices')
  }
  const chosen = report.personalOptions.find(item => item.id === 'chatgpt/gpt-6-astra')
  if (!chosen) throw new Error('The accepted personal model is not selectable')
  // This is a settings draft only. Never submit another Project's preferences.
  await picker.selectOption(chosen.value)
  await expect(picker).toHaveValue(chosen.value)
  report.settingsDraftSelected = true
  report.preferencesSubmitted = false
  await fs.mkdir(output, { recursive: true })
  await picker.screenshot({ path: path.join(output, 'builder-personal-model.png') })
  report.ok = !report.errors.length
} catch (error) {
  report.errors.push(String(error))
  if (inspectedPage) report.diagnostic = await inspectedPage.evaluate(() => {
    const el = [...document.querySelectorAll('[data-webui-widget-id="chat-side-settings"]')]
      .find(item => item.getBoundingClientRect().width > 0)
    const form = el && window.ng?.getComponent(el.querySelector('ada-form-widget') || el)
    const source = form?.optionSources?.get('llmModel')
    return { component: form?.constructor?.name, statusText: el?.querySelector('.option-status')?.textContent,
      options: source?.options?.length, received: source?.received, state: source?.status?.state,
      error: source?.status?.error?.message, selectedRecordId: form?.selectedRecordId,
      recordLoaded: form?.recordLoaded, submitting: form?.submitting }
  })
} finally {
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, 'builder-model-choice.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.ok) process.exitCode = 1
