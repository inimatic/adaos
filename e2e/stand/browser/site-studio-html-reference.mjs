import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'

// Connected journey over customer tools. The original HTML is private material;
// this harness never constructs a design proposal or writes website JSON.
const env = process.env
const required = name => { if (!env[name]) throw Error(name + ' is required'); return env[name] }
const app = required('ADAOS_E2E_SCENARIO_ID')
const project = required('ADAOS_E2E_SITE_PROJECT')
const webspace = required('ADAOS_E2E_WEBSPACE_ID')
const subnet = required('ADAOS_E2E_SUBNET_ID')
const token = required('ADAOS_E2E_HUB_TOKEN')
const output = required('ADAOS_E2E_OUTPUT')
const original = required('ADAOS_E2E_HTML_REFERENCE')
const prompt = await fs.readFile(required('ADAOS_E2E_REFERENCE_PROMPT'), 'utf8')
const hub = env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const client = env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/'
if (env.ADAOS_E2E_ALLOW_REVERSIBLE_SITE_EDITS !== '1') throw Error('Explicit draft edit admission is required')
await fs.mkdir(output, { recursive: true })
const report = { checks: [], errors: [], calls: [], job: env.ADAOS_E2E_RESUME_JOB || '' }
const pass = check => { report.checks.push(check); console.log('PASS ' + check) }
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'ru-RU' })
await context.addInitScript(({ token, hub, webspace, subnet, origin }) => {
  if (location.origin !== origin) return
  window.__ADAOS_DEBUG__ = true; window.__ADAOS_BASE__ = hub; window.__ADAOS_TOKEN__ = token
  for (const [key, value] of Object.entries({ adaos_device_id: 'site-html-reference-journey', adaos_webspace_id: webspace,
    adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
    adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
}, { token, hub, webspace, subnet, origin: new URL(client).origin })
const page = await context.newPage()
page.setDefaultTimeout(30000)
const host = id => page.locator(`[data-webui-widget-id="${id}"]`).last()
report.externalMaps = []
page.on('requestfailed', request => {
  const url = new URL(request.url())
  if (url.hostname.endsWith('yandex.ru')) report.externalMaps.push({ origin: url.origin, path: url.pathname, error: request.failure()?.errorText })
})
page.on('pageerror', error => report.errors.push(error.message))
page.on('response', async response => {
  const url = new URL(response.url())
  if (url.hostname.endsWith('yandex.ru')) report.externalMaps.push({ origin: url.origin, path: url.pathname, status: response.status() })
  if (!response.url().includes('/api/tools/call')) return
  let request; try { request = response.request().postDataJSON() } catch { return }
  if (!String(request?.tool).startsWith(app + '_skill:')) return
  const entry = { tool: request.tool, status: response.status(), runtime: response.headers()['x-adaos-runtime-source'], release: response.headers()['x-adaos-release-digest'] }
  report.calls.push(entry)
  if (String(request.tool).endsWith(':query_preview')) {
    entry.arguments = request.arguments
    console.log('PREVIEW ' + JSON.stringify({ status: response.status(), arguments: request.arguments }))
    const value = await response.json().catch(() => ({}))
    entry.resultKeys = Object.keys(value.result || value)
    entry.schema = (value.result || value).schema
    if (!entry.schema) console.log('PREVIEW_BODY ' + JSON.stringify(value).replaceAll(token, '[redacted]').slice(0, 1800))
    if (response.status() >= 400) {
      entry.error = (await response.text()).replaceAll(token, '[redacted]').slice(0, 1600)
      console.log('PREVIEW_ERROR ' + entry.error)
    }
  }
})
await page.addLocatorHandler(page.locator('.component-updates-backdrop'), () => page.locator('.component-updates-panel__tools button').last().click())
async function api(name, args = {}) {
  const id = 'html-reference-' + crypto.randomUUID()
  const response = await context.request.post(hub + '/api/tools/call', { headers: { 'X-AdaOS-Token': token },
    data: { tool: app + '_skill:' + name, arguments: args, dev: false, request_id: id, idempotency_key: id, timeout: 55,
      context: { webspace_id: webspace, scenario_id: app, current_scenario_id: app } }, timeout: 65000 })
  const value = await response.json()
  if (!response.ok() || value.ok === false || value.result?.ok === false) throw Error(name + ': ' + JSON.stringify(value).slice(0, 1000))
  return value.result ?? value
}
async function tool(id) {
  const button = host('authoring_tools').locator(`[data-command-id="${id}"]:visible`).first()
  if (await button.count()) await button.click()
  else { await host('authoring_tools').locator('button:not([data-command-id]):visible').last().click(); await page.locator(`[data-command-id="${id}"]:visible`).last().click() }
}
async function verifyDashboard(frame, candidate) {
  const section = candidate.source.routes[0].sections.find(item => item.component === 'site.dashboard')
  if (!section) return
  const data = candidate.source.resources.find(item => item.id === section.resource_ref).data
  const dashboard = frame.locator(`[data-webui-widget-id="${section.id}"] .dashboard`)
  const list = dashboard.locator('.view-tabs button').first()
  await dashboard.getByRole('button', { name: 'Сбросить фильтры', exact: true }).click()
  await expect(list).toHaveText('Список · ' + data.rows.length)
  const label = String(data.rows[0][data.labelField])
  await dashboard.locator('input[type="search"]').fill(label)
  await expect(dashboard.locator('.row-link').first()).toBeVisible()
  assert.notEqual(await list.innerText(), 'Список · ' + data.rows.length, 'Text search must narrow the dataset')
  await dashboard.locator('.row-link').first().click()
  await expect(dashboard.locator('.detail h3')).toBeVisible()
  await dashboard.getByRole('button', { name: 'Статистика', exact: true }).click()
  await expect(dashboard.locator('.statistics')).toBeVisible()
  await dashboard.getByRole('button', { name: 'Сбросить фильтры', exact: true }).click()
  await list.click()
  await expect(list).toHaveText('Список · ' + data.rows.length)
  pass('Verified live dashboard search, object card, statistics and reset over the preserved dataset')
}
async function capturePresentation(candidate) {
  const frame = page.frames().find(frame => frame.url().includes('site_preview=' + project))
  assert.ok(frame, 'The integrated public renderer must be present')
  report.presentation = []
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.evaluate(() => document.fullscreenElement ? document.exitFullscreen() : Promise.resolve())
    await page.setViewportSize(viewport)
    await host('site_preview').getByRole('button', { name: 'Expand preview', exact: true }).click()
    await page.waitForFunction(() => !!document.fullscreenElement)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    for (const section of candidate.source.routes[0].sections.filter(section => section.component !== 'site.header')) {
      const widget = frame.locator(`[data-webui-widget-id="${section.id}"]`).first()
      if (!await widget.count()) continue
      await widget.evaluate(element => element.scrollIntoView({ behavior: 'instant', block: 'start' }))
      await page.waitForTimeout(1100)
      const measurement = await widget.evaluate(element => {
        const section = element.querySelector('section,footer,.section-divider') || element
        const style = getComputedStyle(section)
        const portrait = element.querySelector('.card-portrait')
        return { width: innerWidth, height: innerHeight, sectionHeight: section.getBoundingClientRect().height,
          background: style.backgroundColor, font: style.fontFamily, horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
          portrait: portrait ? { width: portrait.getBoundingClientRect().width, height: portrait.getBoundingClientRect().height,
            radius: getComputedStyle(portrait).borderRadius } : null }
      })
      report.presentation.push({ id: section.id, component: section.component, ...measurement })
      await page.screenshot({ path: path.join(output, `${viewport.width}-${section.id}.png`) })
    }
    if (viewport.width === 1440) await verifyDashboard(frame, candidate)
    const drawer = frame.locator('.drawer-trigger')
    if (await drawer.count()) {
      await drawer.click()
      await expect(frame.locator('dialog')).toBeVisible()
      await page.screenshot({ path: path.join(output, `${viewport.width}-drawer.png`) })
      await page.keyboard.press('Escape')
      if (await frame.locator('dialog').evaluate(dialog => dialog.open) && !await page.evaluate(() => !!document.fullscreenElement)) await page.keyboard.press('Escape')
      await expect(frame.locator('dialog')).toBeHidden()
      pass(`Drawer opens and closes with Escape at ${viewport.width}px`)
      if (viewport.width === 1440) {
        const navigation = candidate.source.resources.find(item => item.type === 'site.navigation')?.data
        const entry = navigation?.items?.find(item => String(item.href || '').startsWith('#'))
        if (entry) {
          await drawer.click()
          await frame.locator('dialog').getByRole('link', { name: entry.label, exact: true }).click()
          await expect(frame.locator('dialog')).toBeHidden()
          await expect.poll(() => frame.evaluate(() => location.hash), { timeout: 10000 }).toBe(entry.href)
          pass('Drawer page navigation closes the overlay and reaches the requested anchor')
        }
      }
    }
  }
  await page.evaluate(() => document.fullscreenElement ? document.exitFullscreen() : Promise.resolve())
  await page.setViewportSize({ width: 1600, height: 1000 })
  assert.ok(report.presentation.length, 'No rendered sections were captured')
  pass('Captured the actual integrated renderer on desktop and mobile')
}
async function verifyEditability(scope) {
  const baseline = await api('query_preview', scope)
  const section = baseline.source.routes[0].sections.find(item => item.component === 'site.section')
  const resource = baseline.source.resources.find(item => item.id === section?.resource_ref)
  assert.ok(resource?.data.title, 'A narrative title must remain editable')
  const originalTitle = resource.data.title
  async function editTitle(currentTitle, nextTitle) {
    await tool('structure')
    await host('v_sections').getByText(currentTitle, { exact: true }).first().click()
    await tool('chat')
    await host('chat_context').locator('[data-command-id="properties"]').click()
    const properties = (await api('query_component_fields', { ...scope, id: section.id })).items[0]
    assert.ok(!Object.values(properties.field_paths).some(path => path.startsWith('lockup.artwork')), 'Detailed SVG geometry must stay out of the ordinary form')
    const field = Object.keys(properties.field_paths).find(key => properties.field_paths[key] === 'title')
    assert.ok(field, 'The ordinary form must expose the title')
    const editor = host('component_property_editor')
    await editor.locator(`[data-webui-field-id="${field}"] input`).fill(nextTitle)
    await editor.getByRole('button', { name: 'Сохранить', exact: true }).click()
    await expect.poll(async () => (await api('query_preview', scope)).source.resources.find(item => item.id === resource.id).data.title, { timeout: 60000 }).toBe(nextTitle)
    await page.locator('ion-modal:visible').getByRole('button', { name: 'Close', exact: true }).click()
  }
  await editTitle(originalTitle, originalTitle + ' [проверка редактора]')
  const edited = await api('query_preview', scope)
  const expectedResources = structuredClone(baseline.source.resources)
  expectedResources.find(item => item.id === resource.id).data.title += ' [проверка редактора]'
  assert.deepEqual(edited.source.resources, expectedResources, 'Editing one title must preserve artwork, people and map data')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({ timeout: 90000 })
  await editTitle(originalTitle + ' [проверка редактора]', originalTitle)
  const restored = await api('query_preview', scope)
  for (const key of ['resources', 'assets', 'theme']) assert.deepEqual(restored.source[key], baseline.source[key], `The form must restore exact ${key}`)
  pass('Edited a title through the ordinary form, reopened and restored it without changing geometry, assets or data')
}
try {
  const active = (await api('query_active_project')).item
  assert.equal(active.id, project, 'Selected project must match the explicit test target')
  const scope = { project_id: project, selection_revision: active.revision }
  const before = await api('query_preview', scope)
  await fs.writeFile(path.join(output, 'before.json'), JSON.stringify(before, null, 2))
  const url = new URL(client)
  for (const [key, value] of Object.entries({ scenario_id: app, webspace_id: webspace, webspace, try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 90000 })
  await host('site_identity').waitFor({ timeout: 120000 })
  await expect.poll(() => report.calls.some(call => call.tool.endsWith(':query_active_project') && call.status === 200), { timeout: 180000 }).toBe(true)
  pass('Opened the selected project through the customer interface')
  const filename = path.basename(original)
  let material = (await api('query_source_files', scope)).items.find(item => item.title === filename)
  if (!material) {
    await tool('materials')
    await host('materials_tools').locator('[data-command-id="sources"]').click()
    await host('source_files').getByRole('button', { name: 'Загрузить исходный файл', exact: true }).click()
    const field = id => host('source_editor').locator(`[data-webui-field-id="${id}"] input`).first()
    await field('title').fill(filename)
    await field('folder').fill('prototype')
    await host('source_editor').locator('input[type="file"]').setInputFiles(original)
    await expect(host('source_editor').locator('.file-list')).toContainText(filename)
    await host('source_editor').getByRole('button', { name: 'Добавить', exact: true }).click()
    await expect(host('source_editor')).toBeHidden({ timeout: 60000 })
    material = (await api('query_source_files', scope)).items.find(item => item.title === filename)
  }
  assert.ok(material, 'Private HTML material must be retained')
  const read = await api('read_source_material', { ...scope, id: material.id, expected_revision: material.revision, expected_digest: material.digest })
  assert.equal(read.item.content, (await fs.readFile(original, 'utf8')).slice(0, 16000))
  report.material = { id: material.id, digest: material.digest }
  pass('Retained the exact private HTML reference')
  if (env.ADAOS_E2E_REVIEW_RETAINED_DRAFT === '1') {
    await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({ timeout: 90000 })
    if (env.ADAOS_E2E_CAPTURE_REFERENCE_VIEWPORTS === '1') await capturePresentation(before)
    if (env.ADAOS_E2E_VERIFY_REFERENCE_EDITABILITY === '1') await verifyEditability(scope)
    pass('Reopened the retained design through the customer interface')
  } else {
  await tool('chat')
  await host('site_discussion').locator('textarea').last().waitFor({ timeout: 120000 })
  if (!report.job) {
    const composer = host('site_discussion').locator('textarea').last()
    await composer.fill('@' + filename)
    await host('site_discussion').getByRole('option').filter({ hasText: filename }).click()
    await expect(host('site_discussion').locator('.reference-chips')).toContainText(filename)
    await composer.fill('Прочитай @[' + filename + ']. ' + prompt)
    const sent = page.waitForResponse(response => response.url().includes('/api/tools/call') && String(response.request().postDataJSON()?.tool).endsWith(':discuss'), { timeout: 120000 })
    await host('site_discussion').getByRole('button', { name: 'Отправить сообщение', exact: true }).click()
    const value = await (await sent).json()
    report.job = (value.result ?? value).job_id
    assert.ok(report.job, 'The customer chat must retain the run identity')
    await fs.writeFile(path.join(output, 'job.json'), JSON.stringify({ job: report.job }, null, 2))
    pass('Submitted the design request with a selected material through chat')
  }
  let response
  for (let attempt = 0; attempt < 80; attempt++) {
    const state = await api('resume_discussion', { job_id: report.job })
    console.log('CHAT ' + state.status)
    if (['completed', 'failed', 'cancelled'].includes(state.status)) {
      response = (await api('query_discussion', scope)).messages.find(message => message.id === report.job + '-response')
      break
    }
    await page.waitForTimeout(3000)
  }
  report.response = response?.text
  report.candidate = response?.actions?.find(action => action.action?.params?.preview_change_set_id)?.action.params.preview_change_set_id
  assert.ok(report.candidate, 'The model did not produce a compiled proposal: ' + response?.text)
  const candidate = await api('query_preview', { ...scope, candidate_change_set_id: report.candidate })
  await fs.writeFile(path.join(output, 'candidate.json'), JSON.stringify(candidate, null, 2))
  assert.deepEqual((await api('query_preview', scope)).source.resources, before.source.resources, 'A proposal must not mutate the draft')
  assert.deepEqual(candidate.source.assets, before.source.assets, 'Existing photographs and assets must be preserved')
  for (const resource of before.source.resources) {
    const after = candidate.source.resources.find(item => item.id === resource.id)
    if (['site.dashboard', 'site.map'].includes(resource.type)) assert.deepEqual(after, resource, 'The map and its complete dataset must be preserved')
  }
  const people = source => source.resources.filter(resource => resource.type === 'site.card_collection' && resource.data.variant === 'people')
    .flatMap(resource => resource.data.items).sort((a, b) => String(a.title || a.name).localeCompare(String(b.title || b.name)))
  assert.deepEqual(people(candidate.source), people(before.source), 'Approved people must be preserved across presentation groups')
  pass('Verified candidate isolation and exact preservation of datasets, people and assets')
  // Reload after external continuation so this exact retained response is present;
  // an older proposal in the same conversation must never be selected by position.
  await page.reload({ waitUntil: 'domcontentloaded' })
  await host('site_identity').waitFor({ timeout: 120000 })
  await tool('chat')
  const ownedResponse = host('site_discussion').locator(`[data-message-id="progress:${report.job}"], [data-message-id="${report.job}-response"]`)
  await expect(ownedResponse).toBeVisible({ timeout: 90000 })
  const resultTab = ownedResponse.getByRole('button', { name: 'Результат', exact: true })
  if (await resultTab.count()) await resultTab.click()
  pass('Selected the result of this exact model run')
  await ownedResponse.getByRole('button', { name: 'Посмотреть', exact: true }).click({ timeout: 90000 })
  await expect(host('chat_candidate_review')).toBeVisible()
  await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({ timeout: 90000 })
  await page.screenshot({ path: path.join(output, 'candidate-review.png'), fullPage: true })
  pass('Opened the compiled candidate in the integrated preview')
  if (env.ADAOS_E2E_CAPTURE_REFERENCE_VIEWPORTS === '1') await capturePresentation(candidate)
  if (env.ADAOS_E2E_APPLY_REFERENCE_PROPOSAL === '1') {
    const committed = page.waitForResponse(response => response.url().includes('/api/tools/call') && String(response.request().postDataJSON()?.tool).endsWith(':commit_site_change'), { timeout: 120000 })
    await host('chat_candidate_review').locator('[data-command-id="apply"]').click()
    const value = await (await committed).json()
    assert.notEqual((value.result ?? value).ok, false, 'Apply must succeed')
    await expect(host('chat_candidate_review')).toBeHidden({ timeout: 60000 })
    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({ timeout: 90000 })
    assert.deepEqual((await api('query_preview', scope)).source.resources, candidate.source.resources, 'Applied design must survive reopening')
    const sections = await api('query_sections', scope)
    assert.ok(Array.isArray(sections.items) && sections.items.length, 'Applied sections must remain available to the structure editor')
    pass('Applied the model proposal through the interface and reopened the retained draft')
  }
  if (env.ADAOS_E2E_VERIFY_REFERENCE_EDITABILITY === '1') await verifyEditability(scope)
  }
  assert.equal(report.errors.length, 0, 'The customer interface must not emit browser errors')
  assert.deepEqual(report.calls.filter(call => call.status >= 400 &&
    !(call.status === 409 && call.tool.endsWith(':query_active_project'))), [], 'Customer tools must succeed after initial project admission')
  report.passed = true
} catch (error) {
  report.passed = false
  report.errors.push(String(error).replaceAll(token, '[redacted]'))
  report.previewStatus = await host('site_preview').innerText().catch(() => '')
  await page.screenshot({ path: path.join(output, 'failed.png'), fullPage: true }).catch(() => {})
} finally {
  await fs.writeFile(path.join(output, 'html-reference.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.passed) process.exitCode = 1
