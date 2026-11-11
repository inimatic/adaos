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
const record = name => { report.checks.push({ name, passed: true }); console.log('PASS ' + name) }
async function tool(id) {
  const button = host('authoring_tools').locator(`[data-command-id="${id}"]:visible`).first()
  if (!await button.count()) {
    await host('authoring_tools').locator('button:not([data-command-id]):visible').last().click()
    await page.locator(`[data-command-id="${id}"]:visible`).last().click()
  } else await button.click()
}
async function screenshot(name) { await page.screenshot({ path: path.join(output, name), fullPage: true }) }
async function closedDialog() {
  await expect(page.locator('ion-modal:visible')).toHaveCount(0)
  await expect(page.locator('ion-router-outlet')).not.toHaveAttribute('aria-hidden', 'true')
}
async function closeDialog() {
  await page.locator('ion-modal:visible').getByRole('button', { name: 'Close', exact: true }).click()
  await closedDialog()
}
try {
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 90000 })
  await host('site_preview').waitFor({ state: 'visible', timeout: 90000 })
  await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({ timeout: 90000 })
  const frame = page.frames().find(frame => frame.url().includes('/site-development/adaos/'))
  if (!frame) throw new Error('Isolated AdaOS preview frame is missing')
  await frame.evaluate(() => window.__studioFrameRetained = 'review')
  await frame.evaluate(() => {
    window.__studioInteractions = []
    window.addEventListener('message', event => {
      if (event.data?.type === 'adaos.site_preview.interaction') window.__studioInteractions.push(event.data)
    })
  })
  await expect(host('site_preview').getByRole('button', { name: 'Выбор', exact: true })).toBeVisible()
  await expect(host('site_discussion').locator('.progress-page')).toHaveCount(3)
  record('native-two-pane-preview-and-shared-phase-card')
  await screenshot('prototype-wide.png')

  const divider = page.locator('.workspace-separator:visible').first()
  await expect(divider).toBeEnabled()
  const initialWidth = Number(await divider.getAttribute('aria-valuenow'))
  const bounds = await divider.boundingBox()
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 160)
  await page.mouse.down(); await page.mouse.move(bounds.x + 110, bounds.y + 160, { steps: 8 }); await page.mouse.up()
  expect(Number(await divider.getAttribute('aria-valuenow'))).toBeGreaterThan(initialWidth + 60)
  const savedSizes = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.includes('workspace.size:')))
  expect(savedSizes.length).toBeGreaterThan(0)
  record('pointer-resize-persists-workspace-width')

  const composer = host('site_discussion').locator('textarea').last()
  await composer.fill('Черновик сообщения для выбранного раздела')
  await tool('structure')
  await expect(host('v_sections')).toBeVisible()
  await tool('chat')
  await expect(composer).toHaveValue('Черновик сообщения для выбранного раздела')
  expect(await frame.evaluate(() => window.__studioFrameRetained)).toBe('review')
  record('tool-switch-preserves-chat-draft-and-preview-window')

  await host('site_identity').getByRole('button', { name: 'О проекте', exact: true }).click()
  await expect(page.locator('ion-modal:visible')).toBeVisible()
  await expect(host('target_description')).toBeVisible()
  await expect(host('site_discussion')).toBeVisible()
  for (const id of ['open-projects', 'knowledge']) {
    const button = host('site_identity').locator(`[data-command-id="${id}"]`)
    expect((await button.textContent()).trim()).toBe('')
    await expect(button.locator('ion-icon')).toHaveCount(1)
  }
  // The modal owns the scroll. A single flow must not also introduce region
  // scrollbars inside Ionic's content viewport.
  const aboutModal = page.locator('ion-modal:visible')
  const scrollOwners = await aboutModal.evaluate(async modal => {
    const content = modal.querySelector('ion-content')
    const outer = await content.getScrollElement()
    const nested = [...modal.querySelectorAll('ada-workspace-layout *')].filter(element => {
      const overflow = getComputedStyle(element).overflowY
      return ['auto', 'scroll'].includes(overflow) && element.scrollHeight > element.clientHeight + 2
    })
    return { outer: outer.scrollHeight > outer.clientHeight + 2, nested: nested.map(el => el.className) }
  })
  expect(scrollOwners.outer).toBe(true)
  expect(scrollOwners.nested).toEqual([])
  await host('discuss_target').getByRole('button').scrollIntoViewIfNeeded()
  await expect(host('discuss_target').getByRole('button')).toBeInViewport()
  await screenshot('prototype-about.png')
  await closeDialog()
  await expect(composer).toHaveValue('Черновик сообщения для выбранного раздела')
  await host('site_identity').getByRole('button', { name: 'Открыть или создать сайт', exact: true }).click()
  await expect(host('create_local_site')).toBeVisible()
  await closeDialog()
  record('about-and-project-picker-are-modals-with-chat-retained')

  await host('site_identity').locator('[data-command-id="site-name"]').click()
  await expect(host('target_description')).toBeVisible()
  await closeDialog()
  await expect(host('site_identity')).not.toContainText('Beta')
  await host('site_identity').locator('[data-command-id="version"]').click()
  await expect(host('site_version_details')).toContainText('0.1.2')
  await expect(host('site_version_details')).toContainText('draft-000213')
  await expect(host('site_version_details')).toContainText('Действующие релизы не перенумерованы')
  await screenshot('prototype-version.png')
  await host('version_changes').getByRole('button', { name: 'Показать правки' }).click()
  await closedDialog()
  await expect(host('revision_history')).toBeVisible()
  await page.getByRole('button', { name: 'Прежние задачи по сайту', exact: true }).click()
  await expect(host('legacy_requests_explanation')).toContainText('Обращения посетителей из форм сайта здесь пока не собираются')
  await expect(host('v_requests')).toContainText('Source material received')
  await closeDialog()
  await tool('chat')
  record('project-annotation-version-review-and-legacy-task-access')

  await host('site_preview').getByRole('button', { name: 'Выбор', exact: true }).click()
  await frame.locator('[data-webui-widget-id="hero"] h1').click()
  await expect(host('chat_context')).toContainText('Create an app.')
  const contextHeight = (await host('chat_context').boundingBox()).height
  await host('chat_context').getByRole('button', { name: 'Свойства', exact: true }).click()
  const heading = page.locator('ion-modal:visible [data-webui-field-id="title"] input:visible').first()
  await heading.fill('Локальный заголовок прототипа')
  await screenshot('prototype-properties.png')
  await closeDialog()
  await host('chat_context').getByRole('button', { name: 'Свойства', exact: true }).click()
  await expect(heading).toHaveValue('Локальный заголовок прототипа')
  await host('property_tabs').locator('[data-command-id="items"]').click()
  await expect(host('property-tree-hero')).toBeVisible()
  await expect(host('property-tree-hero')).toContainText('facts')
  await host('property-tree-hero').getByText('Application runtime', { exact: true }).click()
  await expect(page.locator('ion-modal:visible [data-webui-field-id="value"] input:visible')).toHaveValue('One environment for apps and device capabilities.')
  await screenshot('prototype-collection-properties.png')
  await closeDialog()
  expect((await host('chat_context').boundingBox()).height).toBe(contextHeight)
  record('one-row-context-and-properties-modal-retain-draft-and-nested-collections')

  await tool('structure')
  // Select the final source section through its visible title rather than a
  // synthetic iframe message, then assert the preview moved to that section.
  await host('v_sections').getByText('See what exists. Follow what comes next.', { exact: true }).first().click()
  await expect.poll(() => frame.evaluate(() => window.scrollY)).toBeGreaterThan(500)
  record('structure-selection-positions-preview')

  await tool('materials')
  const originalAssetCount = await host('v_assets').locator('.note-card').count()
  await expect(host('v_assets')).not.toContainText('Добавить текст')
  await host('v_assets').getByRole('button', { name: 'Добавить материал', exact: true }).click()
  await expect(host('e_assets')).toBeVisible()
  await expect(host('e_assets').locator('[data-webui-field-id="kind"] input[type="radio"]')).toHaveCount(2)
  await expect(host('e_assets').locator('[data-webui-field-id="alt"] input')).toBeVisible()
  await host('e_assets').locator('[data-webui-field-id="kind"] input[type="radio"]').first().check()
  await host('e_assets').locator('[data-webui-field-id="role"] input[type="radio"]').first().check()
  for (const [field, value] of Object.entries({ alt: 'Review asset', ref: 'https://example.test/review.svg',
    provenance: 'UI review fixture', rights: 'Review only' })) {
    await host('e_assets').locator(`[data-webui-field-id="${field}"] input`).fill(value)
  }
  await host('e_assets').locator('[data-command-id="cmd_asset_create"]').click()
  await expect(page.locator('ion-modal:visible')).toHaveCount(0)
  await host('v_assets').locator('.note-card').filter({ hasText: 'Review asset' }).locator('.note-card-main').click()
  await expect(host('e_assets').locator('[data-webui-field-id="alt"] input')).toHaveValue('Review asset')
  await host('e_assets').locator('[data-webui-field-id="alt"] input').fill('Updated review asset')
  await host('e_assets').locator('[data-command-id="cmd_asset_update"]').click()
  await host('v_assets').locator('.note-card').filter({ hasText: 'Updated review asset' }).locator('.note-card-main').click()
  await host('e_assets').locator('[data-command-id="cmd_asset_delete"]').click()
  const confirmation = page.locator('ion-alert:visible')
  await confirmation.getByRole('button').last().click()
  await expect(host('v_assets').locator('.note-card').filter({ hasText: 'Updated review asset' })).toHaveCount(0)
  await expect(host('v_assets').locator('.note-card')).toHaveCount(originalAssetCount)
  await host('site_materials').getByRole('button', { name: 'Добавить справочный текст', exact: true }).click()
  await expect(host('material_intake')).toBeVisible()
  await closeDialog()
  record('materials-create-update-confirmed-delete-on-isolated-copy')

  await tool('changes')
  await host('change_review').locator('[data-command-id="after"]').click()
  await expect(frame.locator('.hero-statement')).toHaveText('Your workspace. Your software. Your rules.', { timeout: 30000 })
  await host('change_review').locator('[data-command-id="undo"]').click()
  await expect(frame.locator('.hero-statement')).not.toHaveText('Your workspace. Your software. Your rules.', { timeout: 30000 })
  expect(await frame.evaluate(() => window.__studioFrameRetained)).toBe('review')
  record('compiled-candidate-comparison-and-local-undo-retain-frame')

  await tool('publication')
  await expect(host('publication_plan')).toBeVisible()
  await expect(host('publication_plan')).toContainText('Прототип: публикация и доменные операции не запускаются.')
  await expect(host('publication_commands').locator('[data-command-id="prepare"]')).toBeDisabled()
  await expect(host('publication_domain_request')).toBeVisible()
  await expect(host('component_upgrade_policy')).toBeVisible()
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
  report.selection = await page.locator('ada-site-preview-widget').evaluate(element => {
    const component = window.ng?.getComponent(element)
    return { anchor: component?.selectedSourceAnchor, section: component?.state?.getSnapshot()?.selected_sections_id }
  }).catch(() => null)
  const previewFrame = page.frames().find(frame => frame.url().includes('/site-development/adaos/'))
  if (previewFrame) report.preview = await previewFrame.evaluate(() => ({
    messages: window.__studioInteractions, scroll: window.scrollY,
    hosts: Array.from(document.querySelectorAll('ada-public-site-widget-host')).map(el => ({ id: el.dataset.webuiWidgetId,
      top: el.getBoundingClientRect().top, height: el.getBoundingClientRect().height })),
  })).catch(() => null)
  await screenshot('prototype-failure.png').catch(() => {})
  process.exitCode = 1
} finally {
  await fs.writeFile(path.join(output, 'browser-review.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report))
  await browser.close()
}
