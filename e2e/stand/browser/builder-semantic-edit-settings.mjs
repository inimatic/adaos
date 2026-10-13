// Exercise real settings/composer, suppress only the outgoing paid user turn.
import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal } from './prototype-navigation.mjs'

const expect = baseExpect.configure({ timeout: 30_000 })
const output = process.env.ADAOS_E2E_OUTPUT
const host = process.env.ADAOS_E2E_HOST
const project = process.env.ADAOS_E2E_SCENARIO_ID
const report = { ok: false, modelCalls: 0, deliverySuppressed: true, samples: [], errors: [] }
const browser = await chromium.launch({ headless: true })
try {
  await fs.mkdir(output, { recursive: true })
  for (const [name, viewport] of [['wide', { width: 1440, height: 1000 }], ['compact', { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet }) => {
      for (const [key, value] of Object.entries({ adaos_local_hub_url: hub, adaos_local_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
      window.__semanticEditCaptured = []
      const original = WebSocket.prototype.send
      WebSocket.prototype.send = function (data) {
        if (typeof data === 'string' && data.includes('voice.chat.user')) {
          window.__semanticEditCaptured.push(JSON.parse(data))
          return // No request reaches Hub/Root; no model is invoked.
        }
        return original.call(this, data)
      }
    }, { hub: process.env.ADAOS_E2E_HUB_URL, token: process.env.ADAOS_E2E_HUB_TOKEN,
      subnet: process.env.ADAOS_E2E_SUBNET_ID })
    const page = await context.newPage()
    page.on('pageerror', error => report.errors.push(String(error)))
    await installSystemOverlayDismissal(page)
    const url = new URL(process.env.ADAOS_E2E_CLIENT_URL)
    for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo',
      subnet_id: process.env.ADAOS_E2E_SUBNET_ID, webspace_id: host, expected_scenario_id: 'builder', try_local_hub: '1' })) url.searchParams.set(key, value)
    await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
    await page.waitForFunction(() => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === 'builder', { timeout: 60_000 })
    await page.locator('[data-webui-widget-id="design-workbench-header"] [data-command-id="settings"]').filter({ visible: true }).click()
    const form = page.locator('[data-webui-widget-id="design-semantic-edit-settings"]').filter({ visible: true })
    await expect(form).toBeVisible()
    const select = form.locator('select').first()
    await expect(select.locator('option:checked')).toContainText(/Standard|Обычный/)
    await select.selectOption({ index: 2 })
    await expect(select.locator('option:checked')).toContainText(/Bounded LLM edit|Ограниченная LLM-правка/)
    const target = form.locator('select').nth(1)
    await expect(target.locator('option').filter({ hasText: 'view:records-list' })).toHaveCount(1)
    await target.selectOption(await target.locator('option').filter({ hasText: 'view:records-list' }).getAttribute('value'))
    await form.locator('select').nth(2).selectOption({ label: 'title.en' })
    const bounds = await select.boundingBox()
    if (!bounds || bounds.x < 0 || bounds.x + bounds.width > viewport.width + 1) throw new Error('Edit mode selector clips horizontally')
    await page.screenshot({ path: path.join(output, name + '-settings.png') })
    // Close modal by its renderer close button, then use the regular composer.
    await page.locator('ion-modal').filter({ visible: true }).last().getByRole('button', { name: 'Close', exact: true }).click()
    let conversation = page.locator('[data-command-id="conversation"]').filter({ visible: true }).first()
    if (!await conversation.isVisible()) {
      await page.locator('[data-webui-widget-id="design-workbench-header"] [data-command-id="inspect-section"]').click()
      conversation = page.locator('[data-command-option="conversation"]').filter({ visible: true }).first()
    }
    await conversation.click()
    const chat = page.locator('[data-webui-widget-id="design-conversation-full-task"]').filter({ visible: true })
    await expect(chat).toBeVisible()
    await page.waitForFunction(project => {
      const element = document.querySelector('[data-webui-widget-id="design-conversation-full-task"] ada-chat-widget')
      return element && window.ng?.getComponent(element)?.pageState?.getSnapshot?.()?.selectedProjectId === project
    }, project, { timeout: 60_000 })
    const input = chat.locator('textarea, .composer input:not([type="file"])').filter({ visible: true }).first()
    await input.fill('Rename view "Records" to "Work queue" in English')
    await chat.locator('[aria-label="Send message"]').click()
    await page.waitForFunction(() => window.__semanticEditCaptured.length > 0)
    const frames = await page.evaluate(() => window.__semanticEditCaptured)
    const metas = []
    function walk(value) {
      if (!value || typeof value !== 'object') return
      if (value.builder_semantic_edit_mode) metas.push(value)
      for (const child of Object.values(value)) walk(child)
    }
    frames.forEach(walk)
    report.capturedMetadata = metas.map(value => ({ mode: value.builder_semantic_edit_mode, target: value.builder_selected_target,
      writeRef: value.builder_semantic_write_ref, writeProperty: value.builder_semantic_write_property }))
    report.capturedFrameKeys = frames.map(value => Object.keys(value))
    const meta = metas.find(value => value.builder_selected_target?.object_id === project)
    if (meta?.builder_semantic_edit_mode !== 'bounded_ops') throw new Error('Composer did not carry selected target and bounded edit mode')
    if (meta.builder_semantic_write_ref !== 'view:records-list' || meta.builder_semantic_write_property !== 'title.en') throw new Error('Composer widened or omitted selected write scope')
    report.samples.push({ name, defaultMode: 'full_candidate', selectedMode: meta.builder_semantic_edit_mode,
      selectedTarget: meta.builder_selected_target, writeRef: meta.builder_semantic_write_ref,
      writeProperty: meta.builder_semantic_write_property, selectorBounds: bounds, horizontalClipping: false })
    await context.close()
  }
  report.ok = report.samples.length === 2 && report.errors.length === 0
} catch (error) {
  report.errors.push(String(error))
} finally {
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
if (!report.ok) process.exitCode = 1
console.log(JSON.stringify(report))
