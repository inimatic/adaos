// Trusted fixture journey: real Client + unchanged application + owned SQLite.
// Core application admission and provider image quality require separate trials.
import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import crypto from 'node:crypto'
import { isExpectedFixtureRestartReadError, fetchOwnedReadWithOneRetry, observePending } from './controlled-faults.mjs'

const expect = baseExpect.configure({ timeout: 30_000 })
const env = process.env
const layout = env.ADAOS_E2E_LAYOUT
const variant = env.ADAOS_E2E_RETOUCH_VARIANT || 'exact'
if (!['wide', 'compact'].includes(layout)) throw Error('Explicit wide/compact layout required')
if (!['exact', 'edited'].includes(variant)) throw Error('Unknown refusal retry variant')
const fixtureRoot = path.resolve(env.ADAOS_E2E_RETOUCH_FIXTURE)
const scratch = path.resolve('.tmp') + path.sep
if (!fixtureRoot.startsWith(scratch)) throw Error('Owned fixture must be under repository .tmp')
const endpoint = JSON.parse(await fs.readFile(path.join(fixtureRoot, 'fixture-endpoint.json'), 'utf8'))
const marker = JSON.parse(await fs.readFile(path.join(fixtureRoot, 'owned-fixture.json'), 'utf8'))
if (!marker.test || marker.source_digest !== endpoint.source_digest) throw Error('Fixture source ownership mismatch')
const fixtureToken = (await fs.readFile(endpoint.token_file, 'utf8')).trim()
const hub = env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const subnet = env.ADAOS_E2E_SUBNET_ID || 'sn_6acf0c01'
const scenario = env.ADAOS_E2E_SCENARIO_ID || 'natural_retouch'
const webspace = env.ADAOS_E2E_WEBSPACE_ID || 'desktop-dev'
const expectedMaterialization = JSON.parse(env.ADAOS_E2E_EXPECTED_MATERIALIZATION || '{}')
const clientUrl = new URL(env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: webspace, expected_scenario_id: scenario, try_local_hub: '1',
  ...(env.ADAOS_E2E_SPACE_KIND === 'development' ? { space_kind: 'development' } : {}) })) clientUrl.searchParams.set(key, value)
const token = env.ADAOS_E2E_HUB_TOKEN
const output = path.resolve(env.ADAOS_E2E_OUTPUT)
if (!token || !output.startsWith(path.resolve('e2e/artifacts') + path.sep)) throw Error('Explicit local token and E2E artifact output required')
await fs.mkdir(output, { recursive: true })
const hash = value => 'sha256:' + crypto.createHash('sha256').update(typeof value === 'string' || Buffer.isBuffer(value) ? value : JSON.stringify(value)).digest('hex')
const fixture = async (pathname, options = {}) => {
  const response = await fetch(endpoint.base_url + pathname, { ...options,
    headers: { 'X-Retouch-Fixture-Token': fixtureToken, ...(options.headers || {}) } })
  if (!response.ok) throw Error(`Fixture ${pathname}: HTTP ${response.status}`)
  return response
}
const audit = async () => (await fixture('/audit')).json()
const specification = {
  schema: 'adaos.e2e.retouch_journey_spec.v1', layout, variant, source_digest: endpoint.source_digest,
  runner_digest: hash(await fs.readFile(new URL(import.meta.url), 'utf8')),
  scope: 'Synthetic Broker/blob/context boundaries; real Client, handler validation, SQLite, CPU masks/exports. No provider quality or Core admission claim.',
  obligations: ['loading-error-empty', 'upload-jpeg-lossless', 'local-draft-preservation', 'goal-parameters-no-submit',
    'rectangle-protection', 'budget-admission-failure-visible', 'analysis-one-submit-editable-plan', 'approval-progress-reopen-idempotence',
    'completed-run-cost-authority',
    'compare-zoom-pan-difference-download', 'adopt-incremented-original', 'unknown-cancel-reservation-isolation',
    'completed-mask-reanalysis', 'result-input-history'],
}
await fs.writeFile(path.join(output, 'verification-spec.json'), JSON.stringify(specification, null, 2))
const report = { schema: 'adaos.e2e.retouch_journey.v1', specification_digest: hash(specification),
  specification, started_at: new Date().toISOString(), checks: [], errors: [], calls: [], downloads: [], passed: false }
const before = await audit()
if (before.jobs.length || before.runs.length) throw Error('A fresh fixture is required for each layout/trial')
const browser = await chromium.launch({ headless: true })
const viewport = layout === 'wide' ? { width: 1440, height: 1000 } : { width: 390, height: 844 }
const context = await browser.newContext({ viewport })
report.browser_environment = { browser_version: browser.version(), viewport,
  node_version: process.version, platform: process.platform, architecture: process.arch }
await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
await context.addInitScript(({ hub, token, subnet }) => {
  window.__ADAOS_DEBUG__ = true; window.__ADAOS_BASE__ = hub; window.__ADAOS_TOKEN__ = token
  for (const [key, value] of Object.entries({ adaos_device_id: 'owned-retouch-fixture', adaos_hub_base: hub,
    adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token, adaos_local_subnet_id: subnet,
    adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
}, { hub, token, subnet })
let initialReadRelease, firstPhotoRead = false, photoReadFailure = true, restartWindow = false
const initialRead = new Promise(resolve => { initialReadRelease = resolve })
await context.route('**/api/tools/**', async route => {
  const request = route.request(), url = new URL(request.url())
  let body
  try { body = request.postDataJSON() } catch {}
  const isTool = url.pathname === '/api/tools/call' && body?.tool?.startsWith('natural_retouch_skill:')
  const isAttachment = url.pathname.startsWith('/api/tools/natural_retouch_skill/')
  if (url.pathname === '/api/tools/call' && !isTool) {
    report.errors.push('Fixture refused a tool outside the admitted Natural Retouch owner')
    return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'fixture_tool_owner_mismatch' }) })
  }
  if (!isTool && !isAttachment) return route.continue()
  if (isTool && body.tool.endsWith(':query_records') && body.arguments?.kind === 'photos' && photoReadFailure) {
    firstPhotoRead = true; await initialRead
    report.injected_read_failure = {status: 400, reason: 'synthetic_initial_read_failure'}
    return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'synthetic_initial_read_failure' }) })
  }
  const start = Date.now()
  try {
    const headers = { ...request.headers(), 'X-Retouch-Fixture-Token': fixtureToken }
    delete headers['x-adaos-token']; delete headers.authorization; delete headers.cookie
    const response = await fetchOwnedReadWithOneRetry(
      () => route.fetch({ url: endpoint.base_url + url.pathname + url.search, headers, timeout: 60_000 }),
      { method: request.method(), tool: body?.tool }, fault => {
        report.route_retries ||= []
        report.route_retries.push({ ...fault, message: fault.message.replaceAll(fixtureToken, '[redacted]'),
          path: url.pathname, kind: 'owned_read_connection_reset', maximum_retries: 1 })
      })
    const bytes = await response.body()
    report.calls.push({ path: url.pathname, tool: body?.tool, command: body?.arguments?.command,
      request_digest: body ? hash(body) : null, status: response.status(), duration_ms: Date.now() - start,
      runtime_source: response.headers()['x-adaos-runtime-source'] })
    await route.fulfill({ response, body: bytes })
  } catch (error) {
    // Do not retain Playwright's verbose call log: it contains transport headers.
    const message = String(error.message).split('\n')[0].replaceAll(fixtureToken, '[redacted]')
    const fault = { restartWindow, message, method: request.method(), tool: body?.tool }
    report.route_failures ||= []; report.route_failures.push({ ...fault, path: url.pathname, command: body?.arguments?.command })
    if (isExpectedFixtureRestartReadError(fault)) {
      report.expected_faults ||= []
      report.expected_faults.push({ kind: 'owned_fixture_restart', path: url.pathname,
        tool: body?.tool, command: body?.arguments?.command, message })
    } else report.errors.push(`Fixture route failed: ${message}`)
    await route.abort() // Never fall back to a real provider tool.
  }
})
const page = await context.newPage(); page.setDefaultTimeout(30_000)
const assetTasks = []; report.client_assets = []
page.on('response', response => {
  const target = new URL(response.url())
  if (target.origin === new URL(env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/').origin
    && /\.(m?js|css)$/.test(target.pathname) && response.ok()) {
    assetTasks.push(response.body().then(bytes => report.client_assets.push({path: target.pathname,
      digest: hash(bytes), size_bytes: bytes.length})).catch(error => report.errors.push(`Client asset not retained: ${error.message}`)))
  }
})
page.on('pageerror', error => report.errors.push(error.message))
const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
const button = (widget, id) => host(widget).locator(`[data-command-id=${JSON.stringify(id)}]`)
const compare = () => host('retouch_comparison')
const field = (widget, id) => host(widget).locator(`[data-webui-field-id=${JSON.stringify(id)}]`).locator('input,textarea,select').first()
const activeRegion = () => page.locator('ada-layout-region.is-open').last()
async function primary() {
  if (layout !== 'compact') return
  const region = activeRegion(), close = region.locator('.layout-region__header button').first()
  if (await close.isVisible()) {
    await close.click()
    await expect(page.locator('ada-layout-region.is-open')).toHaveCount(0)
  }
}
async function reveal(id) {
  if (await host(id).isVisible()) return
  const trigger = page.locator('.layout-region-trigger[aria-expanded="false"]').first()
  if (await trigger.count()) await trigger.click()
  await expect(host(id)).toBeVisible()
}
async function tab(id) {
  await primary()
  const control = button('retouch_sections', id)
  if (!await control.isVisible()) {
    await host('retouch_sections').locator('button').filter({ hasText: /more|ещё|еще/i }).last().click()
  }
  await control.click()
}
async function ready() {
  await page.waitForFunction(({scenario, expected}) => {
    const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync, value = sync?.materialization || {}
    const identity = {revision: value.materializationRevision, source_fingerprint: value.sourceFingerprint}
    return sync?.materializationReady && value.currentScenario === scenario
      && Object.entries(expected).every(([key, value]) => identity[key] === value)
  }, {scenario, expected: expectedMaterialization}, { timeout: 90_000 })
  const observed = await page.evaluate(() => {
    const value = window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization || {}
    return { revision: value.materializationRevision, source_fingerprint: value.sourceFingerprint,
      application_id: value.applicationId, release_digest: value.applicationReleaseDigest,
      scenario: value.currentScenario }
  })
  expect(observed.scenario).toBe(scenario)
  for (const [key, value] of Object.entries(expectedMaterialization)) expect(observed[key], `Materialization ${key}`).toBe(value)
  report.observed_materializations ||= []; report.observed_materializations.push(observed)
  await expect(host('retouch_sections')).toBeVisible()
}
async function responseFor(action, tool, command, { allowFailure = false } = {}) {
  const response = page.waitForResponse(r => {
    if (new URL(r.url()).pathname !== '/api/tools/call') return false
    const b = r.request().postDataJSON()
    return b?.tool === `natural_retouch_skill:${tool}` && (!command || b.arguments?.command === command)
  }, { timeout: 60_000 })
  await action()
  const http = await response, body = await http.json(), value = body.result ?? body
  if (!allowFailure) expect(http.ok() && body.ok !== false && value.ok !== false).toBe(true)
  expect(http.headers()['x-adaos-runtime-source']).toBe('fixture')
  return value
}
async function command(id, expected) {
  await reveal('retouch_run_commands')
  const expectedFailure = typeof expected === 'string' && expected.endsWith('_failed')
  const result = await responseFor(() => button('retouch_run_commands', id).click(), 'run_command', id, { allowFailure: expectedFailure })
  if (expectedFailure && result.ok === false) {
    expect(typeof result.error).toBe('string')
    expect(String(result.human_message || result.message || '').trim().length).toBeGreaterThan(0)
  }
  if (expected) expect(result.item?.status).toBe(expected)
  return result.item
}
async function step(id, action) {
  report.active = id
  const start = Date.now(), evidence = await action()
  const facts = await audit()
  report.checks.push({ id, passed: true, duration_ms: Date.now() - start, evidence,
    state_digest: hash(facts), jobs: facts.jobs.map(j => ({ request_id: j.request_id, submits: j.submits, polls: j.polls })) })
  await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2))
  console.log(`${layout}: ${id} passed`)
}
const sample = Buffer.from(await (await fixture('/sample-image')).arrayBuffer())
let photo, run, queuedAfterRefusal
async function selectPhoto(item) {
  await tab('photos')
  await host('v_photos').locator(`[data-record-id=${JSON.stringify(item.id)}]`).click()
  await expect(compare().locator('.comparison-image').first()).toBeVisible()
  await primary()
  await compare().getByRole('button', { name: /^(Оригинал|Original)$/ }).click()
}
async function upload(name) {
  await tab('photos')
  await button('v_photos', 'cmd_add_photo').click()
  await expect(host('e_add_photo')).toBeVisible()
  await field('e_add_photo', 'filename').fill(name)
  await field('e_add_photo', 'format').fill('JPEG')
  await field('e_add_photo', 'date').fill('2026-10-17')
  const receipt = observePending(page.waitForResponse(r => r.request().method() === 'PUT' && new URL(r.url()).pathname.endsWith('/upload_attachment/attachments')))
  await host('e_add_photo').locator('input[type=file]').setInputFiles({ name, mimeType: 'image/jpeg', buffer: sample })
  expect((await receipt).ok()).toBe(true)
  const result = await responseFor(() => button('e_add_photo', 'cmd_add_photo').click(), 'mutate_record')
  await expect(host('e_add_photo')).not.toBeVisible()
  return result.item
}
async function masks() {
  await primary()
  await expect(compare().locator('.comparison-image').first()).toBeVisible()
  await compare().getByRole('button', { name: '100%', exact: true }).click()
  await compare().locator('select').selectOption('rectangle')
  await compare().getByRole('button', { name: /^(Рамка фрагмента|Crop)$/ }).click()
  const stage = compare().locator('.stage').first(); await stage.scrollIntoViewIfNeeded()
  const b = await stage.boundingBox(), left = b.x + b.width / 2 - 160, top = b.y + b.height / 2 - 120
  await page.mouse.move(left + .5, top + .5); await page.mouse.down()
  await page.mouse.move(left + 319.5, top + 239.5, { steps: 12 }); await page.mouse.up()
  await expect(compare().getByText(/Маски не сохранены|Unsaved masks/)).toBeVisible()
  await tab('photos'); await tab('retouch')
  await expect(compare().getByText(/Маски не сохранены|Unsaved masks/)).toBeVisible()
  await expect(compare().locator('output.zoom')).toHaveText('100%')
  await compare().getByRole('button', { name: /^(Защита|protect)$/i }).click()
  await compare().locator('input[type=number]').fill('8')
  const protectedStage = await compare().locator('.stage').first().boundingBox()
  await page.mouse.click(protectedStage.x + protectedStage.width / 2 - 110, protectedStage.y + protectedStage.height / 2 - 70)
  await responseFor(() => compare().getByRole('button', { name: /^(Сохранить маски|Save masks)$/ }).click(), 'save_mask')
}
async function parameters({ start = false, strength = '30', expectRejected = false } = {}) {
  await reveal('retouch_run_commands'); await button('retouch_run_commands', 'parameters').click()
  await expect(host('e_edit_params')).toBeVisible()
  await field('e_edit_params', 'strength').fill(strength)
  await field('e_edit_params', 'max_run_usd').fill('1')
  for (const key of ['hair', 'skin', 'wrinkles', 'neck', 'lips', 'blush', 'color']) await field('e_edit_params', `inc_${key}`).setChecked(key === 'skin')
  const closesOnSuccess = await host('e_edit_params').evaluate(node => window.ng?.getComponent(node)?.widget?.inputs?.closeOnSuccess !== false)
  const response = await responseFor(() => button('e_edit_params', start ? 'start_analysis' : 'cmd_update_params').click(), start ? 'run_command' : 'mutate_record', start ? 'analyze' : undefined, { allowFailure: expectRejected })
  if (!start) {
    if (closesOnSuccess) await expect(host('e_edit_params')).not.toBeVisible()
    else {
      await page.locator('ion-modal').last().getByRole('button', { name: /^(Close|Закрыть)$/i }).click()
      await expect(host('e_edit_params')).not.toBeVisible()
    }
  }
  if (start && env.ADAOS_E2E_DIAGNOSTIC_CLOSE_AFTER_START === '1' && await host('e_edit_params').isVisible()) {
    report.supervisor_interventions ||= []
    report.supervisor_interventions.push('Diagnostic continuation: manually closed parameter modal after start; automatic progress focus remains failed.')
    await page.locator('ion-modal').last().getByRole('button', { name: /^(Close|Закрыть)$/i }).click()
  }
  return response
}
try {
  await page.goto(clientUrl.href, { waitUntil: 'domcontentloaded' })
  await ready()
  await step('loading-error-empty', async () => {
    await expect.poll(() => firstPhotoRead).toBe(true)
    await expect(host('v_photos')).toHaveAttribute('data-webui-data-state', 'loading')
    const loading = await host('v_photos').innerText()
    expect(loading).toMatch(/загруз|loading/i)
    expect(loading).not.toMatch(/нет фотографий|no photos/i)
    initialReadRelease()
    await expect(host('v_photos')).toHaveAttribute('data-webui-data-state', /error|unavailable/)
    await expect(host('v_photos').locator('.widget-host-data-status')).toBeVisible()
    photoReadFailure = false
    await host('v_photos').locator('.widget-host-data-status').getByRole('button').click()
    await expect(host('v_photos')).toHaveAttribute('data-webui-data-state', 'ready')
    await expect(host('v_photos').locator('[data-record-id]')).toHaveCount(0)
    return { loading_text: loading, empty_text: await host('v_photos').innerText() }
  })
  await step('upload-jpeg-lossless', async () => {
    photo = await upload(`fixture-${layout}.jpg`); await selectPhoto(photo)
    expect(photo.format).toBe('JPEG'); expect(photo.width).toBe(320); expect(photo.height).toBe(240)
    return { photo_id: photo.id, input_digest: hash(sample), dimensions: [photo.width, photo.height] }
  })
  await step('local-draft-preservation', async () => {
    await reveal('retouch_run_commands'); await button('retouch_run_commands', 'analyze').click()
    const goal = 'Убрать тестовый прыщ; сохранить поры, родинки и геометрию.'
    await field('e_set_goal', 'retouch_goal').fill(goal)
    await page.locator('ion-modal').last().getByRole('button', { name: /^(Close|Закрыть)$/i }).click()
    await button('retouch_run_commands', 'analyze').click()
    await expect(field('e_set_goal', 'retouch_goal')).toHaveValue(goal)
    await responseFor(() => button('e_set_goal', 'save_goal_local').click(), 'mutate_record')
    expect((await audit()).jobs.length).toBe(0)
    return { goal }
  })
  await step('goal-parameters-no-submit', async () => {
    await parameters({ strength: '0' })
    await button('retouch_run_commands', 'parameters').click()
    await expect(field('e_edit_params', 'strength')).toHaveValue('0')
    await expect(field('e_edit_params', 'inc_hair')).not.toBeChecked()
    await page.locator('ion-modal').last().getByRole('button', { name: /^(Close|Закрыть)$/i }).click()
    await expect(host('plan_refine_editor')).not.toBeVisible()
    expect((await audit()).jobs.length).toBe(0)
  })
  await step('rectangle-protection', masks)
  await step('budget-admission-failure-visible', async () => {
    await fixture('/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ exhaust_budget: true }) })
    const response = await parameters({ start: true, expectRejected: true })
    expect(response.ok, 'Known local budget refusal must be a visible rejected start, without a provider job').toBe(false)
    await expect(host('e_edit_params')).toBeVisible()
    await expect(field('e_edit_params', 'strength')).toHaveValue('30')
    expect((await audit()).jobs.length).toBe(0)
    await fixture('/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ exhaust_budget: false }) })
    // Retrying the same draft after a proven pre-submit refusal must work,
    // without a stale settings revision or an ambiguous phantom job.
    if (variant === 'edited') {
      await field('e_edit_params', 'strength').fill('31')
      const retried = await responseFor(() => button('e_edit_params', 'start_analysis').click(), 'run_command', 'analyze')
      queuedAfterRefusal = retried.item
      expect(queuedAfterRefusal.status).toBe('analysis_queued')
      expect((await audit()).jobs.length).toBe(1)
    }
  })
  await step('analysis-one-submit-editable-plan', async () => {
    if (queuedAfterRefusal) run = queuedAfterRefusal
    else if (await host('e_edit_params').isVisible()) {
      run = (await responseFor(() => button('e_edit_params', 'start_analysis').click(), 'run_command', 'analyze')).item
    } else run = (await parameters({ start: true })).item
    expect(run.status).toBe('analysis_queued')
    await expect(host('e_edit_params')).not.toBeVisible()
    const atQueue = await audit(); expect(atQueue.jobs.length).toBe(1)
    if (env.ADAOS_E2E_RESTART_FIXTURE === '1') {
      restartWindow = true
      await fs.writeFile(path.join(fixtureRoot, 'restart-requested.json'), JSON.stringify({ run_id: run.id,
        analysis_request_id: atQueue.jobs[0].request_id, state_digest: hash(atQueue) }))
      const deadline = Date.now() + 60_000
      while (true) {
        try { await fs.access(path.join(fixtureRoot, 'restart-complete.json')); break } catch {}
        if (Date.now() > deadline) throw Error('Owned fixture restart did not complete')
        await new Promise(resolve => setTimeout(resolve, 100))
      }
      const restarted = await audit(); expect(restarted.jobs).toEqual(atQueue.jobs)
      expect(restarted.budget.committed_usd).toBe(atQueue.budget.committed_usd)
      restartWindow = false
      report.fixture_restart = { run_id: run.id, request_id: atQueue.jobs[0].request_id,
        same_jobs: true, same_reservation: true }
    }
    await page.reload({ waitUntil: 'domcontentloaded' }); await ready(); await selectPhoto(photo)
    expect((await audit()).jobs[0].polls).toBe(atQueue.jobs[0].polls)
    run = await command('check_analysis', 'awaiting_approval')
    await reveal('plan_refine_editor')
    await field('plan_refine_editor', 'final_instruction').fill('Only the synthetic blemish. Preserve all other pixels.')
    const saved = await responseFor(() => button('plan_refine_editor', 'save_plan_local').click(), 'run_command', 'save_plan')
    run = saved.item; expect(run.plan_revision).toBe(2)
    const facts = await audit(); expect(facts.jobs.length).toBe(1); expect(facts.jobs[0].submits).toBe(1)
    return { run_id: run.id, analysis_request_id: facts.jobs[0].request_id, plan_revision: run.plan_revision }
  })
  await step('approval-progress-reopen-idempotence', async () => {
    run = await command('approve_plan', 'edit_queued')
    await expect(host('run_tracker_details')).toContainText(/ретуш|retouch/i)
    const saved = await audit(), identity = run.id
    await page.reload({ waitUntil: 'domcontentloaded' }); await ready(); await selectPhoto(photo)
    expect((await audit()).jobs).toEqual(saved.jobs)
    await reveal('tracker_actions')
    await responseFor(() => button('tracker_actions', 'refresh_state').click(), 'run_command', 'refresh')
    const refreshed = await audit()
    expect(refreshed.jobs.length, 'Refresh status is advertised as no new request: it may poll existing jobs, but must never submit the next paid QA or edit phase. Use Continue processing for that explicit transition.').toBe(saved.jobs.length)
    expect(refreshed.jobs.map(({ request_id, kind, submits }) => ({ request_id, kind, submits })))
      .toEqual(saved.jobs.map(({ request_id, kind, submits }) => ({ request_id, kind, submits })))
    run = await command('continue_run', 'qa_queued'); expect(run.id).toBe(identity)
    run = await command('continue_run', 'completed'); expect(run.id).toBe(identity)
    const final = await audit(); expect(final.jobs.length).toBe(3); expect(final.jobs.every(j => j.submits === 1)).toBe(true)
    expect(final.runs.find(item => item.id === identity).checks.protected_pixels_equal).toBe(true)
    return { run_id: identity, jobs: final.jobs }
  })
  await step('completed-run-cost-authority', async () => {
    const facts = await audit()
    const attempts = facts.budget.attempts.filter(row => row.run_id === run.id)
    expect(attempts).toHaveLength(3)
    expect(attempts.every(row => row.status === 'reconciled' && row.cost_basis === 'metered')).toBe(true)
    const expected = attempts.reduce((sum, row) => sum + row.observed_usd, 0)
    await reveal('retouch_run_state')
    let text = ''
    await expect.poll(async () => {
      text = await host('retouch_run_state').innerText()
      const match = text.match(/(?:confirmed spend|подтвержд[её]нный расход):\s*(\d+(?:[.,]\d+)?)\s*USD/iu)
      return match ? Number(match[1].replace(',', '.')) : null
    }).toBe(expected)
    return { run_id: run.id, confirmed_sdk_usd: expected, visible_text: text }
  })
  await step('compare-zoom-pan-difference-download', async () => {
    await tab('results')
    await host('v_results').locator(`[data-record-id="${run.id}-png"]`).click(); await primary()
    await expect(compare()).toBeVisible()
    await compare().getByRole('button', { name: /^(Сравнить|Compare)$/ }).click()
    const images = compare().locator('.comparison-image'); await expect(images).toHaveCount(2)
    await expect.poll(() => images.evaluateAll(nodes => nodes.every(n => n.complete && n.naturalWidth === 320))).toBe(true)
    await compare().getByRole('button', { name: '100%', exact: true }).click()
    await expect(compare().locator('output.zoom')).toHaveText('100%')
    const worlds = () => compare().locator('.world').evaluateAll(nodes => nodes.map(n => n.style.transform))
    const beforePan = await worlds(); expect(beforePan[0]).toBe(beforePan[1])
    await compare().locator('.stage').first().press('ArrowLeft')
    const afterPan = await worlds(); expect(afterPan[0]).toBe(afterPan[1]); expect(afterPan).not.toEqual(beforePan)
    await compare().getByRole('button', { name: /^(Показать изменения|Show changes)$/ }).click()
    await expect(compare().locator('.difference-map')).toHaveCount(2)
    await expect(compare().locator('.difference-info')).toContainText(/без изменений|unchanged/i)
    const differenceBytes = await compare().locator('.difference-map').first().evaluate(async node =>
      [...new Uint8Array(await (await fetch(node.src)).arrayBuffer())])
    await fs.writeFile(path.join(output, 'difference-map.png'), Buffer.from(differenceBytes))
    const download = observePending(page.waitForEvent('download'))
    await compare().getByRole('button', { name: /^(Скачать результат|Download result)$/ }).click()
    const file = await download, name = file.suggestedFilename(), saved = path.join(output, name)
    await file.saveAs(saved); const bytes = await fs.readFile(saved)
    expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a')
    expect([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]).toEqual([320, 240])
    report.downloads.push({ filename: name, digest: hash(bytes), size: bytes.length })
    await host('v_results').locator(`[data-record-id="${run.id}-jpeg"]`).click(); await primary()
    const jpegDownload = observePending(page.waitForEvent('download'))
    await compare().getByRole('button', { name: /^(Скачать результат|Download result)$/ }).click()
    const jpeg = await jpegDownload, jpegName = jpeg.suggestedFilename(), jpegPath = path.join(output, jpegName)
    await jpeg.saveAs(jpegPath); const jpegBytes = await fs.readFile(jpegPath)
    expect(jpegBytes.subarray(0, 2).toString('hex')).toBe('ffd8')
    const geometry = await page.evaluate(async data => {
      const bitmap = await createImageBitmap(new Blob([new Uint8Array(data)], { type: 'image/jpeg' }))
      const size = [bitmap.width, bitmap.height]; bitmap.close(); return size
    }, [...jpegBytes])
    expect(geometry).toEqual([320, 240])
    report.downloads.push({ filename: jpegName, digest: hash(jpegBytes), size: jpegBytes.length })
    return { before_pan: beforePan, after_pan: afterPan, downloaded: [name, jpegName] }
  })
  await step('adopt-incremented-original', async () => {
    const beforeAdopt = await audit()
    await responseFor(() => button('retouch_result_commands', 'adopt_result').click(), 'adopt_result')
    await tab('photos')
    await expect(host('v_photos').locator('[data-record-id]')).toHaveCount(2)
    await expect(host('v_photos')).toContainText(/_001\.png/)
    expect((await audit()).jobs).toEqual(beforeAdopt.jobs)
  })
  await step('unknown-cancel-reservation-isolation', async () => {
    const adopted = host('v_photos').locator('[data-record-id]').filter({ hasText: /_001\.png/ })
    const adoptedId = await adopted.getAttribute('data-record-id')
    await selectPhoto({ id: adoptedId }); await masks()
    await reveal('retouch_run_commands'); await button('retouch_run_commands', 'analyze').click()
    await field('e_set_goal', 'retouch_goal').fill('Отдельная тестовая итерация: только временный дефект кожи.')
    await responseFor(() => button('e_set_goal', 'save_goal_local').click(), 'mutate_record')
    await fixture('/control', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ unknown_submit_phase: 'analysis' }) })
    const uncertain = (await parameters({ start: true })).item
    expect(uncertain.status).toBe('analysis_unknown')
    const reserved = await audit(); expect(reserved.jobs.length).toBe(4)
    expect(reserved.budget.usage_complete).toBe(false)
    await command('cancel', 'cancelled')
    const cancelled = await audit(); expect(cancelled.budget.committed_usd).toBe(reserved.budget.committed_usd)
    await page.reload({ waitUntil: 'domcontentloaded' }); await ready(); await selectPhoto({ id: adoptedId })
    const reopened = await audit(); expect(reopened.jobs).toEqual(cancelled.jobs)
    expect(reopened.budget.committed_usd).toBe(cancelled.budget.committed_usd)
    await reveal('retouch_run_commands')
    await expect(button('retouch_run_commands', 'continue_run')).toHaveAttribute('aria-disabled', 'true')
    await selectPhoto(photo); await reveal('retouch_run_state')
    await expect(host('retouch_run_state')).toContainText(run.id)
    await expect(host('retouch_run_state')).not.toContainText(uncertain.id)
    await selectPhoto({ id: adoptedId })
    await fixture('/control', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ unknown_submit_phase: null, fail_phase: 'analysis' }) })
    const failed = (await parameters({ start: true, strength: '31' })).item
    expect(failed.status).toBe('analysis_queued')
    await command('check_analysis', 'analysis_failed')
    await expect(host('run_tracker_details')).toContainText(/ошибка|error/i)
    const final = await audit(); expect(final.jobs.length).toBe(5)
    expect(final.jobs.every(j => j.submits === 1)).toBe(true)
    return { cancelled_run_id: uncertain.id, failed_run_id: failed.id,
      held_after_cancel_usd: cancelled.budget.committed_usd, usage_complete: final.budget.usage_complete }
  })
  await step('completed-mask-reanalysis', async () => {
    await fixture('/control', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fail_phase:null})})
    await selectPhoto(photo); await tab('retouch'); await primary()
    const originalRun = run
    const oldFacts = await audit(), oldSaved = oldFacts.runs.find(r=>r.id===originalRun.id)
    await compare().getByRole('button',{name:'100%',exact:true}).click()
    await compare().getByRole('button',{name:/^(Защита|protect)$/i}).click()
    await compare().locator('input[type=number]').fill('8')
    const stage=compare().locator('.stage').first(); await stage.scrollIntoViewIfNeeded()
    const b=await stage.boundingBox()
    await page.mouse.click(b.x+b.width/2+50,b.y+b.height/2+30)
    await responseFor(()=>compare().getByRole('button',{name:/^(Сохранить маски|Save masks)$/}).click(),'save_mask')
    await reveal('run_tracker_details')
    let savedTracker=''
    const changedInputs=/устар|вход[а-яё\s]+измен|не\s+проанализирован|измен.{0,60}маск|маск.{0,60}измен|changed|out.?of.?date|new analysis|новый анализ/iu
    await expect.poll(async()=>{
      savedTracker=await host('run_tracker_details').innerText()
      return changedInputs.test(savedTracker)
    }).toBe(true)
    const beforeCheck=await audit()
    await command('check_analysis','completed')
    const banner=host('run_confirm_banner')
    const ackAfter=await banner.isVisible()?await banner.innerText():''
    expect(ackAfter).not.toMatch(/Запрос отправляется|проверяем состояние выбранного|sending request/iu)
    expect((await audit()).jobs).toEqual(beforeCheck.jobs)
    const newRun=(await parameters({start:true,strength:variant==='edited'?'31':'30'})).item
    expect(newRun.id).not.toBe(originalRun.id)
    expect(newRun.status).toBe('analysis_queued')
    await command('check_analysis','awaiting_approval')
    await command('approve_plan','edit_queued')
    await command('continue_run','qa_queued')
    await command('continue_run','completed')
    const final=await audit()
    expect(final.runs.find(r=>r.id===originalRun.id)).toEqual(oldSaved)
    expect(final.runs.find(r=>r.id===newRun.id).status).toBe('completed')
    expect(final.jobs.length).toBe(oldFacts.jobs.length+3)
    expect(final.jobs.every(j=>j.submits===1)).toBe(true)
    await page.reload({waitUntil:'domcontentloaded'});await ready();await selectPhoto(photo)
    expect((await audit()).jobs).toEqual(final.jobs)
    report.revision_probe={original_run_id:originalRun.id,new_run_id:newRun.id,
      tracker_after_mask:savedTracker,ack_after_response:ackAfter,
      completed_with_new_mask:true,old_run_unchanged:true,no_duplicate_submits:true}
    return report.revision_probe
  })

  await step('result-input-history', async () => {
    const baseline = await audit(), oldRun = baseline.runs.find(r=>r.id===run.id)
    await tab('retouch');await reveal('retouch_run_commands')
    await button('retouch_run_commands','analyze').click()
    const nextGoal='Смягчить только тестовое пятно; сохранить исходный рисунок ткани.'
    await field('e_set_goal','retouch_goal').fill(nextGoal)
    await responseFor(()=>button('e_set_goal','save_goal_local').click(),'mutate_record')
    await parameters({strength:'37'})
    expect((await audit()).jobs).toEqual(baseline.jobs)
    await reveal('run_tracker_details')
    await expect(host('run_tracker_details')).toContainText(/устар|вход[а-яё\s]+измен|не\s+проанализирован|changed|new analysis|новый анализ/iu)
    const newest=(await parameters({start:true,strength:'37'})).item
    expect(newest.id).not.toBe(run.id)
    await command('check_analysis','awaiting_approval');await command('approve_plan','edit_queued')
    await command('continue_run','qa_queued');await command('continue_run','completed')
    await tab('results');await primary()
    const provenance=page.locator('[data-webui-label="Параметры выбранного результата"], [data-webui-label="Selected result inputs"]').filter({visible:true}).last()
    await host('v_results').locator(`[data-record-id="${run.id}-png"]`).click()
    await expect(provenance).toBeVisible()
    await expect(provenance).toContainText('Убрать тестовый прыщ; сохранить поры, родинки и геометрию.')
    await expect(provenance).not.toContainText(nextGoal)
    await expect(provenance).toContainText(new RegExp('(?:Сила|Strength)[\\s:]*'+(variant==='edited'?'31':'30'),'iu'))
    await primary()
    const download=observePending(page.waitForEvent('download'))
    await compare().getByRole('button',{name:/^(Скачать результат|Download result)$/}).click()
    const file=await download,pathToOld=path.join(output,'history-original.png');await file.saveAs(pathToOld)
    const oldDigest=hash(await fs.readFile(pathToOld));expect(oldDigest).toBe(report.downloads[0].digest)
    await host('v_results').locator(`[data-record-id="${newest.id}-png"]`).click()
    await expect(provenance).toContainText(nextGoal)
    await expect(provenance).toContainText(/(?:Сила|Strength)[\s:]*37/iu)
    const final=await audit()
    expect(final.runs.find(r=>r.id===run.id)).toEqual(oldRun)
    expect(final.jobs.length).toBe(baseline.jobs.length+3)
    expect(final.jobs.every(j=>j.submits===1)).toBe(true)
    return {old_run_id:run.id,new_run_id:newest.id,old_result_digest:oldDigest,
      older_input_summary_visible:true,new_input_summary_visible:true,old_run_unchanged:true}
  })

  report.passed = report.checks.length === specification.obligations.length && !report.errors.length
  report.incomplete_obligations = specification.obligations.filter(id => !report.checks.some(check => check.id === id))
  report.passed = report.passed && !report.incomplete_obligations.length
} catch (error) {
  report.failure = String(error.message).replaceAll(fixtureToken, '[redacted]').replaceAll(token, '[redacted]')
  report.failure_kind = /expect\(/.test(report.failure) ? 'candidate_postcondition' : 'runner_or_environment'
  console.log(`${layout}: ${report.active} FAILED: ${report.failure.slice(0, 700)}`)
}
finally {
  initialReadRelease()
  report.text = await page.locator('body').innerText().catch(() => '')
  report.final_audit = await audit()
  await Promise.allSettled(assetTasks)
  report.client_assets.sort((a, b) => a.path.localeCompare(b.path))
  report.passed = report.passed && !report.errors.length && !report.failure && report.client_assets.length > 0 && !report.supervisor_interventions?.length
  report.runtime = await page.evaluate(() => window.__ADAOS_DEBUG_STATE__?.()).catch(() => null)
  await page.screenshot({ path: path.join(output, 'final.png'), fullPage: true }).catch(() => {})
  await context.tracing.stop({ path: path.join(output, 'private-trace.zip') })
  await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, failure: report.failure,
  incomplete: report.incomplete_obligations, external_model_calls: report.final_audit.external_model_calls }))
if (!report.passed) process.exitCode = 1
