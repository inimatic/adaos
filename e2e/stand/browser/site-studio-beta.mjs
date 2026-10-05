import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const env = process.env
const scenario = env.ADAOS_E2E_SCENARIO_ID
const skill = env.ADAOS_E2E_SKILL_ID
const webspace = env.ADAOS_E2E_WEBSPACE_ID
const subnet = env.ADAOS_E2E_SUBNET_ID
const token = String(env.ADAOS_E2E_HUB_TOKEN || '').trim()
const hub = env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const client = env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/'
const spaceKind = env.ADAOS_E2E_SPACE_KIND || 'development'
const expectedRuntimeSource = env.ADAOS_E2E_EXPECTED_RUNTIME_SOURCE || null
const requireHistory = env.ADAOS_E2E_REQUIRE_COLLABORATION_HISTORY === '1'
const output = path.resolve(env.ADAOS_E2E_OUTPUT)
const validImage = path.resolve(env.ADAOS_E2E_IMAGE)

if (!scenario || !skill || !webspace || !subnet || !token || !env.ADAOS_E2E_OUTPUT || !env.ADAOS_E2E_IMAGE) {
  throw new Error('Site Studio beta E2E requires an explicit scenario, skill, webspace, subnet, token, output and image')
}
if (!['development', 'workspace'].includes(spaceKind)) throw new Error('Unsupported space kind')
if (expectedRuntimeSource && !['dev', 'trial', 'workspace'].includes(expectedRuntimeSource)) {
  throw new Error('Unsupported expected runtime source')
}
await fs.mkdir(output, { recursive: true })

const url = new URL(client)
for (const [key, value] of Object.entries({
  intent: 'webspace.open', zone: 'lo', subnet_id: subnet, webspace_id: webspace,
  space_kind: spaceKind, expected_scenario_id: scenario, try_local_hub: '1',
})) url.searchParams.set(key, value)

const report = {
  schema: 'adaos.e2e.site_studio.beta.v1',
  scenario,
  skill,
  webspace,
  spaceKind,
  expectedRuntimeSource,
  startedAt: new Date().toISOString(),
  checks: [],
  errors: [],
  warnings: [],
  network: [],
  cleanup: [],
}
const marker = `E2E Site Studio ${Date.now()}`
const toolName = name => `${skill}:${name}`
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'en-US' })
await context.addInitScript(({ hub, token, subnet, webspace }) => {
  window.__ADAOS_DEBUG__ = true
  window.__ADAOS_BASE__ = hub
  window.__ADAOS_TOKEN__ = token
  for (const [key, value] of Object.entries({
    adaos_device_id: 'site-studio-beta-e2e', adaos_webspace_id: webspace,
    adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1',
    adaos_hub_token: token, adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo',
    adaos_last_used_zone: 'lo', adaos_lang: 'en', adaos_env_type: 'dev',
  })) localStorage.setItem(key, value)
}, { hub, token, subnet, webspace })

const pages = new Set()
const instrument = page => {
  pages.add(page)
  page.setDefaultTimeout(30_000)
  page.on('pageerror', error => report.errors.push({ kind: 'page', message: error.message }))
  page.on('console', message => {
    if (message.type() !== 'error') return
    const finding = { kind: 'console', message: message.text() }
    if (/^Failed to load resource:/i.test(message.text())) report.warnings.push(finding)
    else report.errors.push(finding)
  })
  page.on('response', response => {
    if (new URL(response.url()).pathname !== '/api/tools/call') return
    let request = null
    try { request = response.request().postDataJSON() } catch {}
    const runtimeSource = response.headers()['x-adaos-runtime-source'] || null
    report.network.push({
      tool: request?.tool || null,
      status: response.status(),
      runtimeSource,
      releaseDigest: response.headers()['x-adaos-release-digest'] || null,
    })
    if (expectedRuntimeSource && response.ok() && runtimeSource !== expectedRuntimeSource) {
      report.errors.push({
        kind: 'runtime-source',
        message: `Expected ${expectedRuntimeSource}, received ${runtimeSource || 'none'}`,
        tool: request?.tool || null,
      })
    }
  })
  return page
}
const page = instrument(await context.newPage())

const host = (target, id) => target.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
const field = (target, widget, id, control = 'input,textarea,select') =>
  host(target, widget).locator(`[data-webui-field-id=${JSON.stringify(id)}]`).locator(control)
const record = (id, details = {}) => report.checks.push({ id, status: 'passed', ...details })

const waitReady = async target => {
  await target.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  await host(target, 'site_preview').waitFor({ state: 'visible', timeout: 90_000 })
  await target.waitForFunction(id => {
    const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
    return sync?.providerSynced && sync?.materializationReady && sync.materialization.currentScenario === id
  }, scenario, { timeout: 90_000 })
  await target.evaluate(() => document.fonts.ready)
}

const toolResponse = async (target, name, action, timeout = 60_000) => {
  const pending = target.waitForResponse(response => {
    if (new URL(response.url()).pathname !== '/api/tools/call') return false
    try { return response.request().postDataJSON()?.tool === toolName(name) } catch { return false }
  }, { timeout })
  await action()
  const response = await pending
  const body = await response.json()
  return { http: response.status(), body, result: body.result ?? body }
}

const callTool = async (name, args, requestSuffix) => {
  const requestId = `site-studio-e2e:${requestSuffix}:${Date.now()}`
  const response = await context.request.post(`${hub}/api/tools/call`, {
    headers: { 'X-AdaOS-Token': token },
    data: {
      tool: toolName(name),
      arguments: args,
      dev: spaceKind === 'development',
      context: { webspace_id: webspace, scenario_id: scenario, current_scenario_id: scenario },
      request_id: requestId,
      idempotency_key: `skill:${toolName(name)}:${requestId}`,
    },
  })
  const body = await response.json()
  return { http: response.status(), body, result: body.result ?? body }
}

const selectOption = async (target, widget, id, label) => {
  const container = host(target, widget).locator(`[data-webui-field-id=${JSON.stringify(id)}]`)
  if (await container.locator('select').count()) await container.locator('select').selectOption({ label })
  else await container.getByRole('radio', { name: label, exact: true }).check()
}

const targetFieldIds = ['goal', 'audience', 'description', 'constraints', 'open_questions']
const readTargetForm = async target => {
  const form = host(target, 'target_description')
  await expect(field(target, 'target_description', 'revision', 'input')).not.toHaveValue('', { timeout: 30_000 })
  await expect(field(target, 'target_description', 'goal')).not.toHaveValue('', { timeout: 30_000 })
  const values = {}
  for (const id of targetFieldIds) values[id] = await field(target, 'target_description', id).inputValue()
  return { form, values, revision: Number(await field(target, 'target_description', 'revision', 'input').inputValue()) }
}

let targetOriginal = null
let targetRestored = false
let createdSectionId = ''
let createdSectionKey = ''
let createdAssetId = ''
try {
  await waitReady(page)
  await page.waitForFunction(() => {
    const element = document.querySelector('[data-webui-widget-id="site_preview"] ada-site-preview-widget')
    return window.ng?.getComponent?.(element)?.phase === 'ready'
  }, null, { timeout: 90_000 })
  const preview = await host(page, 'site_preview').locator('ada-site-preview-widget').evaluate(element => {
    const component = window.ng?.getComponent?.(element)
    return { phase: component?.phase, digest: component?.bundle?.projection_digest }
  })
  expect(preview.phase).toBe('ready')
  expect(preview.digest).toMatch(/^sha256:[a-f0-9]{64}$/)
  record('preview-verified', { projectionDigest: preview.digest })

  const stalePage = instrument(await context.newPage())
  await waitReady(stalePage)
  await host(stalePage, 'authoring_tabs').locator('[data-command-id="target"]').click()
  await host(stalePage, 'target_description').waitFor({ state: 'visible' })
  const staleTarget = await readTargetForm(stalePage)

  await host(page, 'authoring_tabs').locator('[data-command-id="target"]').click()
  await host(page, 'target_description').waitFor({ state: 'visible' })
  targetOriginal = await readTargetForm(page)
  expect(targetOriginal.revision).toBe(staleTarget.revision)
  await field(page, 'target_description', 'open_questions').fill(marker)
  const saved = await toolResponse(page, 'save_target', () =>
    host(page, 'target_description').locator('[data-command-id="save_target"]').click())
  expect(saved.result.ok).toBe(true)
  expect(saved.result.item.revision).toBe(targetOriginal.revision + 1)
  record('target-save', { revision: saved.result.item.revision })

  const stale = await toolResponse(stalePage, 'save_target', () =>
    host(stalePage, 'target_description').locator('[data-command-id="save_target"]').click())
  expect(stale.result.ok).toBe(false)
  expect(stale.result.error).toBe('revision_conflict')
  record('target-stale-guard')
  await stalePage.close()

  await page.reload({ waitUntil: 'domcontentloaded' })
  await host(page, 'site_preview').waitFor({ state: 'visible', timeout: 90_000 })
  await host(page, 'authoring_tabs').locator('[data-command-id="target"]').click()
  await expect(field(page, 'target_description', 'open_questions')).toHaveValue(marker, { timeout: 30_000 })
  const current = await readTargetForm(page)
  for (const id of targetFieldIds) await field(page, 'target_description', id).fill(targetOriginal.values[id])
  const restored = await toolResponse(page, 'save_target', () =>
    host(page, 'target_description').locator('[data-command-id="save_target"]').click())
  expect(restored.result.ok).toBe(true)
  targetRestored = true
  record('target-reload-and-restore', { revision: restored.result.item.revision })

  await host(page, 'authoring_tabs').locator('[data-command-id="canvas"]').click()
  const created = await toolResponse(page, 'mutate_records', () =>
    host(page, 'v_sections').locator('[data-command-id="add-section"]').click())
  expect(created.result.ok).toBe(true)
  createdSectionId = created.result.item.id
  createdSectionKey = created.result.item.key
  const sectionRow = () => host(page, 'v_sections').locator(
    `.collection-focus-item[data-focus-ref=${JSON.stringify(createdSectionId)}]`)
  await expect(sectionRow()).toBeVisible({ timeout: 30_000 })
  await sectionRow().click()
  await field(page, 'e_section', 'heading').fill(marker)
  await field(page, 'e_section', 'body', 'textarea').fill('Self-cleaning Site Studio beta browser journey.')
  const updated = await toolResponse(page, 'mutate_records', () =>
    host(page, 'e_section').locator('[data-command-id="cmd_section_update"]').click())
  expect(updated.result.item.heading).toBe(marker)
  record('section-create-and-update', { record: createdSectionId, revision: updated.result.item.revision })

  await page.reload({ waitUntil: 'domcontentloaded' })
  await host(page, 'site_preview').waitFor({ state: 'visible', timeout: 90_000 })
  await expect(sectionRow()).toBeVisible({ timeout: 30_000 })
  await sectionRow().click()
  await expect(field(page, 'e_section', 'heading')).toHaveValue(marker, { timeout: 30_000 })

  const moved = await toolResponse(page, 'mutate_records', () =>
    sectionRow().getByRole('button', { name: `Move up: ${createdSectionKey}`, exact: true }).click())
  expect(moved.result.ok).toBe(true)
  record('section-shared-reorder-command', { order: moved.result.item.order, input: 'button' })

  const remark = `${marker}: preserve this deleted-block anchor.`
  await expect(field(page, 'block_remark', 'key')).toHaveValue(createdSectionId, { timeout: 30_000 })
  await field(page, 'block_remark', 'remark_text', 'textarea').fill(remark)
  const posted = await toolResponse(page, 'post_remark', () =>
    host(page, 'block_remark').locator('[data-command-id="send_block_remark"]').click(), 140_000)
  expect(posted.result.status).toBe('recorded')
  const feedbackId = posted.result.job_id
  const feedbackRow = host(page, 'block_feedback').locator('.collection-focus-item').filter({ hasText: remark })
  await expect(feedbackRow).toHaveCount(1, { timeout: 30_000 })
  const resolved = await toolResponse(page, 'set_feedback', () =>
    feedbackRow.locator('[data-command-id="resolve"]').click())
  expect(resolved.result.status).toBe('resolved')
  await expect(feedbackRow.locator('[data-command-id="reopen"]')).toBeVisible()
  const reopened = await toolResponse(page, 'set_feedback', () =>
    feedbackRow.locator('[data-command-id="reopen"]').click())
  expect(reopened.result.status).toBe('open')
  await expect(feedbackRow.locator('[data-command-id="resolve"]')).toBeVisible()
  const finalResolve = await toolResponse(page, 'set_feedback', () =>
    feedbackRow.locator('[data-command-id="resolve"]').click())
  expect(finalResolve.result.status).toBe('resolved')
  record('anchored-feedback-lifecycle', { feedback: feedbackId })

  const deleted = await toolResponse(page, 'mutate_records', async () => {
    await host(page, 'e_section').locator('[data-command-id="delete-section"]').click()
    await page.locator('ion-alert').last().getByRole('button', { name: 'Delete section', exact: true }).click()
  })
  expect(deleted.result.ok).toBe(true)
  createdSectionId = ''
  const retained = await callTool('query_feedback', { block_id: deleted.result.id }, 'deleted-feedback')
  const anchor = retained.result.items.find(item => item.id === feedbackId)
  expect(anchor.block_snapshot.heading).toBe(marker)
  expect(anchor.status).toBe('resolved')
  record('deleted-block-anchor-retained', { feedback: feedbackId })

  await host(page, 'prototype-sections').locator('[data-command-id="inspector_assets"]').click()
  await host(page, 'e_assets').waitFor({ state: 'visible' })
  await selectOption(page, 'e_assets', 'kind', 'Image')
  const fileField = host(page, 'e_assets').locator('[data-webui-field-id="file_local"]')
  const fileInput = fileField.locator('input[type="file"]')
  await fileInput.setInputFiles({ name: 'not-an-image.txt', mimeType: 'text/plain', buffer: Buffer.from('not an image') })
  await expect(fileField.locator('.field-error')).toBeVisible({ timeout: 30_000 })
  record('invalid-image-rejected')

  const uploadPending = page.waitForResponse(response => response.request().method() === 'PUT'
    && new URL(response.url()).pathname.includes('/upload_attachment/attachments'), { timeout: 60_000 })
  await fileInput.setInputFiles(validImage)
  const upload = await uploadPending
  expect(upload.ok()).toBe(true)
  await field(page, 'e_assets', 'ref').fill(`asset://site-studio-e2e-${Date.now()}`)
  await field(page, 'e_assets', 'alt').fill('Site Studio beta verification image')
  await field(page, 'e_assets', 'usage').fill('Owned upload lifecycle E2E')
  const asset = await toolResponse(page, 'mutate_records', () =>
    host(page, 'e_assets').locator('[data-command-id="cmd_asset_create"]').click())
  expect(asset.result.ok).toBe(true)
  expect(asset.result.item.file_local).toContain('/read_attachment/attachments/')
  createdAssetId = asset.result.item.id
  const assetRow = host(page, 'v_assets').locator(
    `.collection-focus-item[data-focus-ref=${JSON.stringify(createdAssetId)}]`)
  await expect(assetRow).toBeVisible({ timeout: 30_000 })
  await assetRow.click()
  const assetDeleted = await toolResponse(page, 'mutate_records', async () => {
    await host(page, 'e_assets').locator('[data-command-id="cmd_asset_delete"]').click()
    await page.locator('ion-alert').last().getByRole('button', { name: 'Delete asset', exact: true }).click()
  })
  expect(assetDeleted.result.ok).toBe(true)
  createdAssetId = ''
  record('owned-image-create-and-confirmed-delete')

  await host(page, 'authoring_tabs').locator('[data-command-id="discussion"]').click()
  const chat = host(page, 'site_discussion')
  await expect(chat.locator('ion-textarea textarea')).toBeVisible()
  await host(page, 'discussion_scope').locator('[data-command-id="site"]').click()
  if (requireHistory) {
    await expect(chat.locator('.msg').first()).toBeVisible({ timeout: 30_000 })
    expect(await chat.locator('.msg').count()).toBeGreaterThanOrEqual(2)
    const assistantText = (await chat.locator('.msg .bubble-text').last().innerText()).trim()
    expect(assistantText.length).toBeGreaterThan(20)
    await host(page, 'authoring_tabs').locator('[data-command-id="target"]').click()
    const rejected = host(page, 'description_proposals').locator('.collection-focus-item').filter({ hasText: /rejected/i }).first()
    await expect(rejected).toBeVisible({ timeout: 30_000 })
    await expect(rejected.locator('[data-command-id="apply"]')).toHaveCount(0)
    await expect(rejected.locator('[data-command-id="reject"]')).toHaveCount(0)
    record('retained-llm-history-and-terminal-proposal', { replyLength: assistantText.length })
  } else {
    record('discussion-surface-ready')
  }

  await host(page, 'prototype-sections').locator('[data-command-id="inspector_tabs"]').click()
  await host(page, 'v_sections').locator('.collection-focus-item[data-focus-ref="hero"]').click()
  await expect(field(page, 'e_section', 'heading')).not.toHaveValue('', { timeout: 30_000 })

  const wideGeometry = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))
  expect(wideGeometry.scrollWidth).toBeLessThanOrEqual(wideGeometry.clientWidth)
  await page.screenshot({ path: path.join(output, 'wide-final.png'), fullPage: true, animations: 'disabled' })
  record('wide-no-horizontal-overflow', wideGeometry)

  const compact = instrument(await context.newPage())
  await compact.setViewportSize({ width: 390, height: 844 })
  await waitReady(compact)
  await host(compact, 'authoring_tabs').locator('[data-command-id="discussion"]').click()
  await expect(host(compact, 'site_discussion').locator('ion-textarea textarea')).toBeVisible()
  await host(compact, 'authoring_tabs').locator('[data-command-id="target"]').click()
  await expect(host(compact, 'target_description')).toBeVisible()
  await expect(field(compact, 'target_description', 'revision', 'input')).not.toHaveValue('', { timeout: 30_000 })
  await expect(field(compact, 'target_description', 'goal')).not.toHaveValue('', { timeout: 30_000 })
  const compactGeometry = await compact.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))
  expect(compactGeometry.scrollWidth).toBeLessThanOrEqual(compactGeometry.clientWidth)
  await compact.screenshot({ path: path.join(output, 'compact-final.png'), fullPage: true, animations: 'disabled' })
  record('compact-collaboration-surfaces', compactGeometry)
  await compact.close()

  report.status = 'passed'
} catch (error) {
  report.status = 'failed'
  report.fatal = `${error.name}: ${error.message}`
  await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true, animations: 'disabled' }).catch(() => {})
} finally {
  if (!targetRestored && targetOriginal) {
    try {
      const current = await callTool('query_target', {}, 'cleanup-target-read')
      const restored = await callTool('save_target', {
        payload: targetOriginal.values,
        expected_revision: current.result.item.revision,
      }, 'cleanup-target-save')
      report.cleanup.push({ target: restored.result.ok === true ? 'restored' : 'failed' })
    } catch (error) {
      report.cleanup.push({ target: `failed: ${error.message}` })
    }
  }
  if (createdSectionId) {
    try {
      const current = await callTool('query_sections', { resource: 'sections', id: createdSectionId }, 'cleanup-section-read')
      const item = current.result.item || current.result.items?.[0]
      if (item) await callTool('mutate_records', {
        resource: 'sections', operation: 'delete', id: createdSectionId,
        expected_revision: item.revision, confirmed: true,
      }, 'cleanup-section-delete')
      report.cleanup.push({ section: 'deleted' })
    } catch (error) {
      report.cleanup.push({ section: `failed: ${error.message}` })
    }
  }
  if (createdAssetId) {
    try {
      const current = await callTool('query_assets', { resource: 'assets', id: createdAssetId }, 'cleanup-asset-read')
      const item = current.result.item || current.result.items?.[0]
      if (item) await callTool('mutate_records', {
        resource: 'assets', operation: 'delete', id: createdAssetId,
        expected_revision: item.revision, confirmed: true,
      }, 'cleanup-asset-delete')
      report.cleanup.push({ asset: 'deleted' })
    } catch (error) {
      report.cleanup.push({ asset: `failed: ${error.message}` })
    }
  }
  report.finishedAt = new Date().toISOString()
  await fs.writeFile(path.join(output, 'journey.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8')
  for (const openPage of pages) await openPage.close().catch(() => {})
  await browser.close()
}

console.log(JSON.stringify({ status: report.status, checks: report.checks.length, errors: report.errors.length,
  cleanup: report.cleanup, output }, null, 2))
if (report.status !== 'passed' || report.errors.length) process.exitCode = 1
