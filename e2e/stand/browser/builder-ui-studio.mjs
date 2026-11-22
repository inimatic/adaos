import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal, dismissSystemOverlays, revealPrototypeWidget } from './prototype-navigation.mjs'

const env = process.env
const hub = env.ADAOS_E2E_HUB_URL
const token = env.ADAOS_E2E_HUB_TOKEN
const subnet = env.ADAOS_E2E_SUBNET_ID || 'sn_6acf0c01'
const builderHost = env.ADAOS_E2E_WEBSPACE_ID || 'desktop-dev'
const builderRevision = env.ADAOS_E2E_BUILDER_REVISION || '006'
const output = path.resolve(env.ADAOS_E2E_OUTPUT || 'e2e/artifacts/builder/ui-studio')
const client = env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/'
const projectId = env.ADAOS_E2E_PROJECT_ID || 'small_cities_analytics'

if (env.ENV_TYPE !== 'dev' || !hub || !token) {
  throw new Error('Explicit local DEV authorization is required')
}

await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const report = {
  schema: 'adaos.e2e.builder_ui_studio.v1',
  projectId,
  builderHost,
  checks: [],
  requests: [],
  errors: [],
  passed: false,
}

const record = (check, details = {}) => report.checks.push({ check, passed: true, ...details })

const addRuntimeContext = async (context, host) => {
  await context.addInitScript(({ hub, token, subnet, host }) => {
    window.__ADAOS_DEBUG__ = true
    window.__ADAOS_BASE__ = hub
    window.__ADAOS_TOKEN__ = token
    for (const [key, value] of Object.entries({
      adaos_device_id: 'e2e-builder-ui-studio',
      adaos_webspace_id: host,
      adaos_lang: 'ru',
      adaos_hub_base: hub,
      adaos_local_hub_base: hub,
      adaos_try_local_hub: '1',
      adaos_hub_token: token,
      adaos_local_subnet_id: subnet,
      adaos_selected_zone: 'lo',
      adaos_last_used_zone: 'lo',
    })) localStorage.setItem(key, value)
  }, { hub, token, subnet, host })
}

const openContext = async (host, viewport) => {
  const context = await browser.newContext({ viewport, locale: 'ru-RU', colorScheme: 'dark' })
  await addRuntimeContext(context, host)
  return context
}

const skillName = response => {
  const data = response.request().postData() || ''
  const match = data.match(/builder_sdk_control_skill(?:\.|:)([a-z0-9_]+)/i)
  return match?.[1] || null
}

const skillResult = body => {
  if (body?.result && typeof body.result === 'object') return body.result
  if (body?.data?.result && typeof body.data.result === 'object') return body.data.result
  return body
}

const appUrl = ({ host, scenario, revision }) => {
  const url = new URL(client)
  for (const [key, value] of Object.entries({
    intent: 'webspace.open',
    zone: 'lo',
    subnet_id: subnet,
    webspace_id: host,
    space_kind: 'development',
    scenario_id: scenario,
    expected_scenario_id: scenario,
    expected_revision: revision,
    try_local_hub: '1',
  })) {
    if (value) url.searchParams.set(key, value)
  }
  return url.href
}

let builderPage
let previewPage
try {
  const builderContext = await openContext(builderHost, { width: 1600, height: 1000 })
  builderPage = await builderContext.newPage()
  await installSystemOverlayDismissal(builderPage)
  builderPage.setDefaultTimeout(30_000)
  builderPage.on('pageerror', error => report.errors.push({ surface: 'builder', message: error.message }))
  builderPage.on('response', async response => {
    const name = skillName(response)
    if (!name?.includes('ui_studio')) return
    report.requests.push({
      surface: 'builder',
      tool: name,
      status: response.status(),
      response: await response.json().catch(() => null),
    })
  })

  const widget = id => builderPage.locator(`[data-webui-widget-id="${id}"]`).filter({ visible: true }).first()
  const command = id => builderPage.locator(`[data-command-id="${id}"]`).filter({ visible: true }).first()
  await builderPage.goto(appUrl({ host: builderHost, scenario: 'builder', revision: builderRevision }), {
    waitUntil: 'domcontentloaded', timeout: 60_000,
  })
  await widget('design-current-work').waitFor({ timeout: 60_000 })
  await builderPage.waitForFunction(() => {
    const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
    return sync?.materializationReady && sync.materialization.currentScenario === 'builder'
  }, undefined, { timeout: 60_000 })
  await dismissSystemOverlays(builderPage)

  const selectedProject = async () => widget('design-current-work').locator('ada-details-widget').evaluate(element =>
    window.ng?.getComponent(element)?.state?.getSnapshot?.()?.selectedProjectId || null)
  if (await selectedProject() !== projectId) {
    await command('applications').click()
    const picker = builderPage.locator('ion-modal').filter({ has: builderPage.locator('[data-webui-widget-id="project-picker-table"]') }).last()
    await picker.waitFor({ state: 'visible' })
    const search = picker.locator('[data-webui-widget-id="project-picker-table"] input').first()
    await search.fill(projectId)
    let row = picker.locator('[data-webui-widget-id="project-picker-table"] tbody tr').filter({ hasText: projectId }).first()
    if (!await row.count()) {
      await search.fill('Малые города')
      row = picker.locator('[data-webui-widget-id="project-picker-table"] tbody tr').filter({ hasText: 'Малые города' }).first()
    }
    await dismissSystemOverlays(builderPage)
    await row.click()
    await picker.waitFor({ state: 'hidden' })
    await expect.poll(selectedProject, { timeout: 60_000 }).toBe(projectId)
  }
  record('small_cities_project_selected')

  const [studioResponse] = await Promise.all([
    builderPage.waitForResponse(response => skillName(response) === 'get_ui_studio', { timeout: 60_000 }),
    command('ui-studio').click(),
  ])
  await widget('ui-studio-intro').waitFor({ state: 'visible' })
  const studioEnvelope = await studioResponse.json()
  let studio = skillResult(studioEnvelope)
  if (env.ADAOS_E2E_CREATE_BENCHMARK === '1' || !studio?.experiment_id) {
    const benchmarkReply = builderPage.waitForResponse(
      response => skillName(response) === 'create_ui_studio_benchmark',
      { timeout: 120_000 },
    )
    await widget('ui-studio-actions').locator('[data-command-id="benchmark"]').filter({ visible: true }).click()
    studio = skillResult(await (await benchmarkReply).json())
    record('live_benchmark_compiled')
  }
  await expect(widget('ui-studio-state')).toContainText(studio.experiment_id, { timeout: 60_000 })
  await expect(widget('ui-studio-options').locator('tbody tr')).toHaveCount(2, { timeout: 60_000 })
  await expect(widget('ui-studio-prompt')).toBeVisible()
  if (!studio?.experiment_id || studio.selected_score < 90 || studio.context_truncated) {
    throw new Error(`Studio projection is not acceptance-ready: ${JSON.stringify(studio)}`)
  }
  report.experiment = {
    id: studio.experiment_id,
    selectedOption: studio.selected_option_id,
    score: studio.selected_score,
    contextUsage: studio.context_usage,
    contextTruncated: studio.context_truncated,
  }
  const variants = (studio.options || []).map(option => ({
    id: option.option_id,
    label: option.label,
    score: Number(option.score),
    passed: option.passed === true,
    hardFailures: Number(option.hard_failures || 0),
    selected: option.selected === true,
  }))
  expect(variants).toHaveLength(2)
  expect(variants.every(option => option.passed && option.hardFailures === 0)).toBe(true)
  const bestScore = Math.max(...variants.map(option => option.score))
  const selectedVariant = variants.find(option => option.selected)
  expect(selectedVariant?.score).toBe(bestScore)
  report.evaluation = { variants, selected: selectedVariant, bestScore }
  record('variants_evaluated_and_best_selected', report.evaluation)
  record('typed_experiment_loaded', report.experiment)

  await builderPage.reload({ waitUntil: 'domcontentloaded', timeout: 60_000 })
  await widget('design-current-work').waitFor({ timeout: 60_000 })
  await builderPage.waitForFunction(() => {
    const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
    return sync?.materializationReady && sync.materialization.currentScenario === 'builder'
  }, undefined, { timeout: 60_000 })
  await expect.poll(selectedProject, { timeout: 60_000 }).toBe(projectId)
  if (!await widget('ui-studio-intro').isVisible()) await command('ui-studio').click()
  await expect(widget('ui-studio-state')).toContainText(studio.experiment_id, { timeout: 60_000 })
  await expect(widget('ui-studio-state')).toContainText(studio.goal, { timeout: 60_000 })
  await expect(widget('ui-studio-options').locator('tbody tr')).toHaveCount(2, { timeout: 60_000 })
  record('latest_experiment_restored_after_builder_reload', { experimentId: studio.experiment_id })

  await builderPage.evaluate(() => document.fonts.ready)
  await builderPage.screenshot({ path: path.join(output, 'builder-studio-wide.png'), fullPage: true, animations: 'disabled' })
  const builderMetrics = await builderPage.evaluate(() => ({
    viewportWidth: innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    activeView: document.querySelector('[data-command-id="ui-studio"]')?.getAttribute('aria-pressed'),
  }))
  report.builder = { url: builderPage.url(), metrics: builderMetrics }
  expect(builderMetrics.documentWidth).toBeLessThanOrEqual(builderMetrics.viewportWidth + 1)
  record('studio_wide_no_document_overflow', builderMetrics)

  const registryRow = widget('ui-studio-options').locator('tbody tr').filter({ hasText: 'Реестр' }).first()
  const registrySelectionReply = builderPage.waitForResponse(
    response => skillName(response) === 'select_ui_studio_option',
    { timeout: 60_000 },
  )
  await registryRow.locator('[data-command-id="select-option"]').filter({ visible: true }).click()
  const registrySelection = skillResult(await (await registrySelectionReply).json())
  expect(registrySelection.selected_option_id).toBe('registry')
  await expect(widget('ui-studio-state')).toContainText('92.1', { timeout: 60_000 })
  await registryRow.locator('[data-command-id="preview-option"]').filter({ visible: true }).waitFor()
  record('registry_variant_selected_through_ui')

  const builderUrlBeforeRegistryPreview = builderPage.url()
  const registryPreviewReply = builderPage.waitForResponse(
    response => skillName(response) === 'preview_ui_studio',
    { timeout: 180_000 },
  )
  const registryPopupReply = builderPage.waitForEvent('popup', { timeout: 30_000 })
  await registryRow.locator('[data-command-id="preview-option"]').filter({ visible: true }).click()
  const registryPage = await registryPopupReply
  await installSystemOverlayDismissal(registryPage)
  registryPage.setDefaultTimeout(30_000)
  registryPage.on('pageerror', error => report.errors.push({ surface: 'registry-preview', message: error.message }))
  const registryPreview = skillResult(await (await registryPreviewReply).json())
  const registryHost = registryPreview?.preview?.preview_webspace_id
  const registryRevision = registryPreview?.staged_revision
  expect(registryPreview?.preview?.target?.scenario_id).toBe(projectId)
  expect(registryRevision?.endsWith('-REGISTRY')).toBe(true)
  await registryPage.waitForFunction(({ expectedHost, expectedScenario }) => {
    const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
    const url = new URL(location.href)
    return sync?.materializationReady
      && sync.materialization.currentScenario === expectedScenario
      && url.searchParams.get('webspace_id') === expectedHost
  }, { expectedHost: registryHost, expectedScenario: projectId }, { timeout: 90_000 })
  await registryPage.locator('[data-webui-widget-id="overview-map"]').filter({ visible: true }).first().waitFor({ timeout: 90_000 })
  await registryPage.locator('[data-webui-widget-id="overview-table"]').filter({ visible: true }).first().waitFor({ timeout: 90_000 })
  await expect(registryPage.getByText(/^(?:Loading data|Загрузка данных)(?:\.{3}|…)?$/i)).toHaveCount(0, { timeout: 60_000 })
  await registryPage.locator('.coordinate-map').waitFor({ state: 'visible', timeout: 30_000 })
  await expect.poll(
    () => registryPage.locator('[data-webui-widget-id="overview-table"] tbody tr').filter({ visible: true }).count(),
    { timeout: 60_000 },
  ).toBeGreaterThan(5)
  for (const id of ['indicator', 'period', 'theme']) {
    await expect(registryPage.locator(`[data-webui-widget-id="${id}"]`).filter({ visible: true }).first())
      .not.toContainText('Выберите вариант', { timeout: 60_000 })
  }
  expect(builderPage.url()).toBe(builderUrlBeforeRegistryPreview)
  await expect(widget('ui-studio-intro')).toBeVisible()
  await registryPage.evaluate(() => document.fonts.ready)
  const registryMetrics = await registryPage.evaluate(() => {
    const rectangle = id => {
      const element = [...document.querySelectorAll(`[data-webui-widget-id="${id}"]`)]
        .find(candidate => candidate.getBoundingClientRect().width > 0)
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    }
    return {
      viewportWidth: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      map: rectangle('overview-map'),
      table: rectangle('overview-table'),
      inspector: rectangle('ui-studio-selection-inspector'),
    }
  })
  expect(registryMetrics.documentWidth).toBeLessThanOrEqual(registryMetrics.viewportWidth + 1)
  await registryPage.screenshot({ path: path.join(output, 'small-cities-registry-wide.png'), fullPage: true, animations: 'disabled' })
  report.registryComparison = { host: registryHost, revision: registryRevision, metrics: registryMetrics }
  record('registry_preview_opened_in_new_tab_builder_preserved')
  record('registry_variant_previewed_through_ui', report.registryComparison)
  await registryPage.close()

  const atlasSelectionReply = builderPage.waitForResponse(
    response => skillName(response) === 'select_ui_studio_option',
    { timeout: 60_000 },
  )
  const atlasRow = widget('ui-studio-options').locator('tbody tr').filter({ hasText: 'Атлас' }).first()
  await atlasRow.locator('[data-command-id="select-option"]').filter({ visible: true }).click()
  const atlasSelection = skillResult(await (await atlasSelectionReply).json())
  expect(atlasSelection.selected_option_id).toBe('atlas')
  await expect(widget('ui-studio-state')).toContainText('98.5', { timeout: 60_000 })
  await atlasRow.locator('[data-command-id="preview-option"]').filter({ visible: true }).waitFor()
  record('best_variant_reselected_through_ui')

  const builderUrlBeforePreview = builderPage.url()
  const previewReply = builderPage.waitForResponse(response => skillName(response) === 'preview_ui_studio', { timeout: 180_000 })
  const previewPopupReply = builderPage.waitForEvent('popup', { timeout: 30_000 })
  await atlasRow.locator('[data-command-id="preview-option"]').filter({ visible: true }).click()
  previewPage = await previewPopupReply
  await installSystemOverlayDismissal(previewPage)
  previewPage.setDefaultTimeout(30_000)
  previewPage.on('pageerror', error => report.errors.push({ surface: 'atlas-preview', message: error.message }))
  const previewEnvelope = await (await previewReply).json()
  const preview = skillResult(previewEnvelope)
  const previewHost = preview?.preview?.preview_webspace_id
  const revision = preview?.staged_revision
  const target = preview?.preview?.target
  if (!preview?.ok || !previewHost || !revision || target?.scenario_id !== projectId || target?.revision !== revision) {
    throw new Error(`Preview response lost exact target identity: ${JSON.stringify(preview)}`)
  }
  report.preview = { host: previewHost, revision, target, currentPointerUnchanged: preview.current_pointer_unchanged }
  expect(preview.current_pointer_unchanged).toBe(true)
  record('exact_exp_preview_materialized', report.preview)

  await previewPage.waitForFunction(({ expectedHost, expectedScenario }) => {
    const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
    const url = new URL(location.href)
    return sync?.materializationReady
      && sync.materialization.currentScenario === expectedScenario
      && url.searchParams.get('webspace_id') === expectedHost
  }, { expectedHost: previewHost, expectedScenario: projectId }, { timeout: 90_000 })
  expect(builderPage.url()).toBe(builderUrlBeforePreview)
  await expect(widget('ui-studio-intro')).toBeVisible()
  report.preview.transition = { builder: builderPage.url(), preview: previewPage.url() }
  record('preview_opened_new_tab_without_leaving_builder', report.preview.transition)
  await previewPage.waitForFunction(expected => {
    const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
    return sync?.materializationReady && sync.materialization.currentScenario === expected
  }, projectId, { timeout: 90_000 })
  const previewWidget = id => previewPage.locator(`[data-webui-widget-id="${id}"]`).filter({ visible: true }).first()
  const previewCommand = id => previewPage.locator(`[data-command-id="${id}"]`).filter({ visible: true }).first()
  await previewWidget('overview-map').waitFor({ state: 'visible', timeout: 90_000 })
  await previewWidget('overview-table').waitFor({ state: 'visible', timeout: 90_000 })
  await previewWidget('ui-studio-selection-inspector').waitFor({ state: 'visible', timeout: 90_000 })
  await previewPage.evaluate(() => document.fonts.ready)
  await expect(previewPage.getByText(/^(?:Loading data|Загрузка данных)(?:\.{3}|…)?$/i)).toHaveCount(0, { timeout: 60_000 })
  await previewPage.locator('.coordinate-map').waitFor({ state: 'visible', timeout: 30_000 })
  await expect(previewPage.locator('.navigation-intent-backdrop')).toHaveCount(0)
  record('preview_has_no_blocking_navigation_prompt')
  expect(await previewPage.locator('.coordinate-map__point').count()).toBeGreaterThan(5)
  for (const id of ['indicator', 'period', 'theme']) {
    await expect(previewWidget(id)).not.toContainText('Выберите вариант', { timeout: 60_000 })
  }

  const inspector = previewWidget('ui-studio-selection-inspector')
  await previewWidget('overview-table').locator('tbody tr').filter({ hasText: 'Кохма' }).first().click()
  await expect(inspector).toContainText('Кохма', { timeout: 30_000 })
  await expect(previewPage.locator('.coordinate-map__point.is-selected')).toHaveAttribute('aria-label', /Кохма/i, { timeout: 30_000 })
  await previewPage.locator('.coordinate-map__point').evaluateAll(points => {
    const target = points.find(point => /Пл[ёе]с/i.test(point.getAttribute('aria-label') || ''))
    if (!(target instanceof HTMLElement)) throw new Error('Plyos map point is unavailable')
    target.click()
  })
  await expect(inspector).toContainText(/Пл[ёе]с/i, { timeout: 30_000 })
  record('table_and_map_share_stable_city_selection')

  const tableAcceptance = async (id, { minimumRows = 1, verifyToggle = false } = {}) => {
    const table = previewWidget(id)
    await table.waitFor({ state: 'visible', timeout: 60_000 })
    await expect.poll(
      () => table.locator('tbody tr').filter({ visible: true }).count(),
      { timeout: 60_000 },
    ).toBeGreaterThanOrEqual(minimumRows)
    const sortHeaders = table.locator('button.sort-header').filter({ visible: true })
    await expect.poll(() => sortHeaders.count(), { timeout: 30_000 }).toBeGreaterThan(0)
    const result = {
      rows: await table.locator('tbody tr').filter({ visible: true }).count(),
      sortHeaders: await sortHeaders.count(),
    }
    if (verifyToggle) {
      const header = sortHeaders.first()
      const heading = header.locator('xpath=..')
      await header.click()
      await expect(heading).toHaveAttribute('aria-sort', 'ascending')
      const ascendingFirstRow = await table.locator('tbody tr').filter({ visible: true }).first().innerText()
      await header.click()
      await expect(heading).toHaveAttribute('aria-sort', 'descending')
      await expect.poll(
        () => table.locator('tbody tr').filter({ visible: true }).first().innerText(),
        { timeout: 10_000 },
      ).not.toBe(ascendingFirstRow)
      result.toggle = 'ascending-to-descending'
    }
    return result
  }

  await previewCommand('passport').click()
  const passportAcceptance = await tableAcceptance('passport-indicators')

  await previewCommand('history').click()
  await previewWidget('history-chart').waitFor({ state: 'visible', timeout: 60_000 })
  await expect(previewWidget('history-chart').locator('.summary-list')).toHaveCount(0)
  const historyAcceptance = await tableAcceptance('history-table', { minimumRows: 5, verifyToggle: true })
  await previewPage.screenshot({ path: path.join(output, 'small-cities-atlas-history-wide.png'), fullPage: true, animations: 'disabled' })

  await previewCommand('compare').click()
  const compareAcceptance = await tableAcceptance('compare-choices', { minimumRows: 5 })

  await previewCommand('data').click()
  const dataAcceptance = await tableAcceptance('coverage')

  await previewCommand('overview').click()
  await previewWidget('overview-map').waitFor({ state: 'visible', timeout: 60_000 })
  await previewWidget('overview-table').waitFor({ state: 'visible', timeout: 60_000 })
  const overviewAcceptance = await tableAcceptance('overview-table', { minimumRows: 5 })
  report.viewAcceptance = {
    overview: overviewAcceptance,
    passport: passportAcceptance,
    history: historyAcceptance,
    compare: compareAcceptance,
    data: dataAcceptance,
    chartPointSummary: 'hidden-by-default',
  }
  record('all_views_and_table_sorting_accepted', report.viewAcceptance)

  const metrics = async () => previewPage.evaluate(() => {
    const rectangle = id => {
      const element = [...document.querySelectorAll(`[data-webui-widget-id="${id}"]`)]
        .find(candidate => candidate.getBoundingClientRect().width > 0)
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    }
    return {
      viewportWidth: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      documentHeight: document.documentElement.scrollHeight,
      map: rectangle('overview-map'),
      table: rectangle('overview-table'),
      inspector: rectangle('ui-studio-selection-inspector'),
      debug: window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization || null,
    }
  })

  const wide = await metrics()
  report.preview.wide = wide
  expect(wide.documentWidth).toBeLessThanOrEqual(wide.viewportWidth + 1)
  expect(wide.map?.width || 0).toBeGreaterThan(500)
  expect(wide.map?.height || 0).toBeGreaterThan(420)
  expect(wide.table?.width || 0).toBeGreaterThan(320)
  expect(Math.abs((wide.map?.x || 0) - (wide.table?.x || 0))).toBeGreaterThan(100)
  await previewPage.screenshot({ path: path.join(output, 'small-cities-atlas-wide.png'), fullPage: true, animations: 'disabled' })
  record('atlas_wide_map_registry_co_visible', wide)

  const previewText = await previewPage.locator('body').innerText()
  if (/Data source failed|Unknown skill tool|runtime is out of sync|Источник данных недоступен/i.test(previewText)) {
    throw new Error('Preview contains a runtime or data-source failure')
  }

  await previewPage.setViewportSize({ width: 390, height: 844 })
  await previewPage.waitForTimeout(500)
  const compactMap = await metrics()
  expect(compactMap.documentWidth).toBeLessThanOrEqual(compactMap.viewportWidth + 1)
  expect(compactMap.map?.width || 0).toBeLessThanOrEqual(390)
  await previewPage.screenshot({ path: path.join(output, 'small-cities-atlas-compact.png'), fullPage: true, animations: 'disabled' })

  await revealPrototypeWidget(previewPage, [], { id: 'overview-table' })
  await previewWidget('overview-table').waitFor({ state: 'visible' })
  await expect.poll(() => previewWidget('overview-table').evaluate(element => element.getBoundingClientRect().y), { timeout: 5_000 }).toBeLessThan(844)
  const compactRegistry = await metrics()
  expect(compactRegistry.documentWidth).toBeLessThanOrEqual(compactRegistry.viewportWidth + 1)
  expect(compactRegistry.table?.width || 0).toBeGreaterThan(300)
  expect(compactRegistry.table?.width || 0).toBeLessThanOrEqual(390)
  expect(compactRegistry.table?.y || Number.MAX_SAFE_INTEGER).toBeLessThan(844)
  await previewPage.screenshot({ path: path.join(output, 'small-cities-atlas-compact-registry.png'), fullPage: true, animations: 'disabled' })
  const closePanel = async () => {
    const close = previewPage.locator('ada-layout-region.is-compact.is-open .layout-region__header button[aria-label]').filter({ visible: true }).last()
    if (await close.count()) await close.click()
    else await previewPage.keyboard.press('Escape')
  }
  await closePanel()
  await revealPrototypeWidget(previewPage, [], { id: 'ui-studio-selection-inspector' })
  await previewWidget('ui-studio-selection-inspector').waitFor({ state: 'visible' })
  await expect.poll(() => previewWidget('ui-studio-selection-inspector').evaluate(element => element.getBoundingClientRect().y), { timeout: 5_000 }).toBeLessThan(844)
  const compactInspector = await metrics()
  expect(compactInspector.documentWidth).toBeLessThanOrEqual(compactInspector.viewportWidth + 1)
  expect(compactInspector.inspector?.width || 0).toBeGreaterThan(300)
  expect(compactInspector.inspector?.width || 0).toBeLessThanOrEqual(390)
  expect(compactInspector.inspector?.y || Number.MAX_SAFE_INTEGER).toBeLessThan(844)
  await previewPage.screenshot({ path: path.join(output, 'small-cities-atlas-compact-inspector.png'), fullPage: true, animations: 'disabled' })
  report.preview.compact = { map: compactMap, registry: compactRegistry, inspector: compactInspector }
  record('atlas_compact_panels_are_publicly_reachable_without_horizontal_overflow', report.preview.compact)

  report.passed = true
} catch (error) {
  report.failure = error.stack || error.message
  if (builderPage && !builderPage.isClosed()) {
    await builderPage.screenshot({ path: path.join(output, 'builder-failure.png'), fullPage: true }).catch(() => {})
  }
  if (previewPage && !previewPage.isClosed()) {
    await previewPage.screenshot({ path: path.join(output, 'preview-failure.png'), fullPage: true }).catch(() => {})
  }
} finally {
  await browser.close()
}

await fs.writeFile(path.join(output, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8')
console.log(JSON.stringify({ passed: report.passed, failure: report.failure, experiment: report.experiment, preview: report.preview && {
  host: report.preview.host, revision: report.preview.revision,
} }))
if (!report.passed) process.exitCode = 1
