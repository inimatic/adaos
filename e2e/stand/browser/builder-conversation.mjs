import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const checkpoint = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CHECKPOINT, 'utf8'))
const created = checkpoint.steps.find(step => step.id === 'create')?.output
if (process.env.ENV_TYPE !== 'dev' || !checkpoint.cleanup?.test || checkpoint.cleanup.acceptance !== 'not_approved') {
  throw new Error('An owned, unapproved DEV test checkpoint is required')
}
const topic = created.topic
const hub = process.env.ADAOS_E2E_HUB_URL
const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const builderWebspace = process.env.ADAOS_E2E_BUILDER_WEBSPACE
if (!builderWebspace || !topic?.source_webspace_id || !hub || !token || !subnet) throw new Error('Explicit scoped DEV Builder target required')
const run = path.resolve(path.dirname(process.env.ADAOS_E2E_CHECKPOINT), '../..')
const grading = JSON.parse(await fs.readFile(path.join(run, 'evidence/grading', `${checkpoint.case_id}-attempt-${String(checkpoint.repetition).padStart(2, '0')}-input.json`), 'utf8'))
const prompts = JSON.parse(grading.messages.find(message => message.role === 'user').content).user_turns
if (prompts.length < 2) throw new Error('Creation and design ingress are required')
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output, { recursive: true })
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: builderWebspace, space_kind: 'development', expected_scenario_id: 'builder', try_local_hub: '1' })) url.searchParams.set(key, value)
const report = { project: created.scenario_id, topic: topic.thread_id, prompts, samples: [], passed: false }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-conversation', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace: builderWebspace })
    const page = await context.newPage()
    page.setDefaultTimeout(20_000)
    const sample = { layout, errors: [] }
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(error.message))
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await page.waitForFunction(() => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync.materialization?.currentScenario === 'builder'
      }, undefined, { timeout: 60_000 })
      const suffix = created.scenario_id.match(/e2e[a-f0-9]{12}/)?.[0]
      if (!suffix) throw new Error('Missing owned test identity')
      if (layout === 'wide') {
      await page.getByRole('button', { name: /choose project|выбрать проект/i }).first().click()
      const picker = page.locator('ion-modal').filter({ has: page.locator('ada-table-widget') }).last()
      await expect(picker.locator('ada-selector-widget .selector__control')).toBeVisible()
      await picker.locator('ada-selector-widget .selector__control').click()
      await page.locator('button.selector__option').filter({ hasText: /^(Test applications|Тестовые приложения)$/ }).click()
      await picker.locator('ada-table-widget input[type=search]').fill(suffix)
      const row = picker.locator('tr.row-selectable').filter({ hasText: suffix })
      await expect(row).toHaveCount(1, { timeout: 20_000 })
      await row.click()
      await expect(picker).toBeHidden()
      }
      await expect(page.locator('[data-webui-widget-id="project-header"]')).toContainText(suffix, { timeout: 20_000 })
      const conversation = page.locator('[data-webui-widget-id="node-views"] [data-command-id="conversation"]')
      await expect(conversation).toBeVisible()
      await conversation.click()
      const chat = page.locator('[data-webui-widget-id="builder-chat"]')
      await expect(chat).toBeVisible()
      for (const prompt of prompts) await expect(chat).toContainText(prompt)
      const transcript = await chat.innerText()
      for (const prompt of prompts) if (transcript.split(prompt).length !== 2) throw new Error('Duplicate or missing original prompt')
      sample.transcript = transcript
      sample.passed = !sample.errors.length
    } catch (error) { sample.failure = error.message; sample.passed = false }
    await page.screenshot({ path: path.join(output, `${layout}.png`), fullPage: true })
    await fs.writeFile(path.join(output, `${layout}.txt`), await page.locator('body').innerText(), 'utf8')
    await context.close()
  }
  report.passed = report.samples.every(sample => sample.passed)
} finally {
  await fs.writeFile(path.join(output, 'conversation.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
  await browser.close()
}
if (!report.passed) process.exitCode = 1
