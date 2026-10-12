// Submit one ordinary request through the installed Builder's visible chat.
// No skill invocation, state injection, or fabricated conversation rows.
import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal } from './prototype-navigation.mjs'

const expect = baseExpect.configure({ timeout: 30_000 })
const output = process.env.ADAOS_E2E_OUTPUT
const instruction = process.env.ADAOS_E2E_INSTRUCTION
const revision = process.env.ADAOS_E2E_EXPECTED_REVISION
const hub = process.env.ADAOS_E2E_HUB_URL, token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID, host = process.env.ADAOS_E2E_HOST
const project = process.env.ADAOS_E2E_SCENARIO_ID
const observeOnly = process.env.ADAOS_E2E_OBSERVE_ONLY === '1'
const viewport = process.env.ADAOS_E2E_COMPACT === '1' ? { width: 390, height: 844 } : { width: 1440, height: 1000 }
const report = { ok: false, mode: observeOnly ? 'history_usability' : 'submit', viewport, instruction, expectedRevision: revision, checks: [], errors: [], reads: [] }
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport })
await context.addInitScript(({ hub, token, subnet }) => {
  window.__ADAOS_DEBUG__ = true
  window.__ADAOS_BASE__ = hub
  window.__ADAOS_TOKEN__ = token
  for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-semantic-request', adaos_hub_base: hub,
    adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
    adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
}, { hub, token, subnet })
const page = await context.newPage()
page.on('response', async response => {
  try {
    if (!response.url().endsWith('/api/tools/call')) return
    const request = response.request().postDataJSON()
    if (!/^builder_sdk_control_skill[:.](get_workbench|get_project|get_state)$/.test(request?.tool || '')) return
    const body = await response.json()
    const identities = []
    const walk = (value, depth = 0) => {
      if (depth > 4 || !value || typeof value !== 'object' || Array.isArray(value)) return
      if (value.object_id || value.topic_id) identities.push({ objectId: value.object_id, objectType: value.object_type, topicId: value.topic_id })
      for (const key of ['result', 'data', 'output', 'view']) walk(value[key], depth + 1)
    }
    walk(body)
    report.reads.push({ tool: request.tool, objectId: request.arguments?.object_id,
      webspaceId: request.arguments?.webspace_id || request.arguments?._meta?.webspace_id, identities })
  } catch {}
})
page.on('pageerror', error => report.errors.push(String(error)))
try {
  await installSystemOverlayDismissal(page)
  const url = new URL(process.env.ADAOS_E2E_CLIENT_URL)
  for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
    webspace_id: host, expected_scenario_id: 'builder', try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  await page.waitForFunction(() => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === 'builder', { timeout: 60_000 })
  let conversationControl = page.locator('[data-command-id="conversation"]').filter({ visible: true }).first()
  if (!await conversationControl.isVisible()) {
    await page.locator('[data-webui-widget-id="design-workbench-header"] [data-command-id="inspect-section"]').click()
    conversationControl = page.locator('[data-command-option="conversation"]').filter({ visible: true }).first()
  }
  await conversationControl.click()
  const chat = page.locator('[data-webui-widget-id="design-conversation-full-task"]').filter({ visible: true })
  await expect(chat).toBeVisible()
  await page.waitForFunction(project => {
    const element = document.querySelector('[data-webui-widget-id="design-conversation-full-task"] ada-chat-widget')
    const component = element && window.ng?.getComponent(element)
    const state = component?.pageState?.getSnapshot?.()
    return state?.selectedProjectId === project && state?.builderTopicId === `prompt-project:scenario:${project}`
  }, project, { timeout: 60_000 })
  await expect(chat.locator('.composer')).toBeVisible()
  const previousIds = observeOnly ? [] : await chat.locator('ada-chat-widget').evaluate(element =>
    window.ng.getComponent(element).messages.map(message => message.id))
  const input = chat.locator('textarea, .composer input:not([type="file"])').filter({ visible: true }).first()
  const started = performance.now()
  if (!observeOnly) {
    await input.fill(instruction)
    await expect(chat.locator('[aria-label="Send message"]')).toBeEnabled()
    await chat.locator('[aria-label="Send message"]').click()
  }
  await page.waitForFunction(({ previousIds, revision, instruction, project }) => {
    const element = document.querySelector('[data-webui-widget-id="design-conversation-full-task"] ada-chat-widget')
    const component = element && window.ng?.getComponent(element)
    const messages = component?.messages || []
    const receipt = messages.find(message => !previousIds.includes(message.id)
      && message.text === `Label operation applied: revision ${revision}.`
      && message.thread_id === `prompt-project:scenario:${project}`)
    return receipt && messages.some(message => message.from === 'user'
      && message.id === receipt.source_message_id && message.text === instruction)
  }, { previousIds, revision, instruction, project }, { timeout: 90_000 })
  await expect(chat).toContainText(`Label operation applied: revision ${revision}.`, { timeout: 90_000 })
  if (!observeOnly) report.sendToReceiptMs = Math.round(performance.now() - started)
  await expect(chat).toContainText(instruction)
  report.receipt = await chat.locator('ada-chat-widget').evaluate((element, { previousIds, revision, project }) => {
    const message = window.ng.getComponent(element).messages.find(message => !previousIds.includes(message.id)
      && message.text === `Label operation applied: revision ${revision}.`
      && message.thread_id === `prompt-project:scenario:${project}`)
    return { id: message.id, sourceMessageId: message.source_message_id, threadId: message.thread_id }
  }, { previousIds, revision, project })
  const visibleReceipt = chat.getByText(`Label operation applied: revision ${revision}.`, { exact: true }).last()
  await visibleReceipt.scrollIntoViewIfNeeded()
  await expect(visibleReceipt).toBeInViewport()
  report.receiptGeometry = await visibleReceipt.evaluate(element => {
    const clip = { left: 0, top: 0, right: innerWidth, bottom: innerHeight }
    for (let node = element.parentElement; node; node = node.parentElement) {
      const style = getComputedStyle(node), box = node.getBoundingClientRect()
      if (/auto|scroll|hidden|clip/.test(style.overflowX)) {
        clip.left = Math.max(clip.left, box.left + node.clientLeft)
        clip.right = Math.min(clip.right, box.left + node.clientLeft + node.clientWidth)
      }
      if (/auto|scroll|hidden|clip/.test(style.overflowY)) {
        clip.top = Math.max(clip.top, box.top + node.clientTop)
        clip.bottom = Math.min(clip.bottom, box.top + node.clientTop + node.clientHeight)
      }
    }
    const range = document.createRange()
    range.selectNodeContents(element)
    const rects = [...range.getClientRects()].filter(rect => rect.width && rect.height)
    return { visible: rects.length > 0 && rects.every(rect => rect.left >= clip.left - 1
      && rect.right <= clip.right + 1 && rect.top >= clip.top - 1 && rect.bottom <= clip.bottom + 1), clip }
  })
  if (!report.receiptGeometry.visible) throw new Error('Semantic receipt text is clipped inside conversation')
  report.checks.push(observeOnly ? 'existing real user request and exact receipt remain in history' : 'visible composer submitted real user request',
    'installed Builder returned semantic revision receipt', 'receipt glyphs are readable within clipping ancestors')
  await page.screenshot({ path: path.join(output, 'conversation.png'), fullPage: true })
  report.ok = report.errors.length === 0
} catch (error) {
  report.error = String(error)
  report.observed = await page.evaluate(() => {
    const element = document.querySelector('[data-webui-widget-id="design-conversation-full-task"] ada-chat-widget')
    const component = element && window.ng?.getComponent(element)
    const state = component?.pageState?.getSnapshot?.() || {}
    return { selectedProjectId: state.selectedProjectId, builderTopicId: state.builderTopicId,
      builderThreadId: state.builderThreadId, messages: (component?.messages || []).map(message =>
        ({ id: message.id, from: message.from, text: message.text, thread: message.thread_id })) }
  })
  await fs.writeFile(path.join(output, 'body.txt'), await page.locator('body').innerText())
  await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true })
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
}
console.log(JSON.stringify(report))
if (!report.ok) process.exitCode = 1
