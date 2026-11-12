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
const report = { scope: 'Native authored-WebUI prototype; no real LLM or publication', checks: [], errors: [], writes: [], sourceOperations: [], sourceErrors: [] }
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
  if (request.url().includes('/api/resources/operate')) {
    const body = request.postDataJSON()
    if (body?.resource_type !== 'prototype.site_studio_workbench_20261111.sources') report.writes.push('unexpected-resource-write')
    report.sourceOperations.push({ operation: body?.operation_id, id: body?.record_id, expected_revision: body?.expected_revision })
  }
  if (!request.url().includes('/api/tools/call')) return
  const body = request.postDataJSON()
  if (/site_studio_test_.*_skill[:.](mutate|commit|propose|publish|prepare|select|create|set_)/.test(body?.tool || '')) report.writes.push(body.tool)
})
page.on('response', response => {
  if (response.url().includes('/api/resources/operate') && response.status() >= 400) report.sourceErrors.push(response.status())
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
  await divider.press('Home')
  await expect(host('site_identity').locator('[data-command-id]')).toHaveCount(3)
  const identityBounds = await host('site_identity').locator('[data-command-id]').evaluateAll(buttons => buttons.map(button => {
    const rect = button.getBoundingClientRect(); return { left: rect.left, right: rect.right }
  }))
  expect(identityBounds[0].right).toBeLessThanOrEqual(identityBounds[1].left + 1)
  expect(identityBounds[1].right).toBeLessThanOrEqual(identityBounds[2].left + 1)
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
  await host('site_identity').locator('[data-command-id="knowledge"]').click()
  await host('project_details_tabs').locator('[data-command-id="versions"]').click()
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
  await host('materials_tools').locator('[data-command-id="sources"]').click()
  await host('site_materials').getByRole('button', { name: 'Добавить справочный текст', exact: true }).click()
  await expect(host('material_intake')).toBeVisible()
  await closeDialog()
  record('materials-create-update-confirmed-delete-on-isolated-copy')

  await expect(host('source_files')).toContainText('Прототип AdaOS.html')
  await host('source_files').getByText('Прототип AdaOS.html', { exact: true }).click()
  await expect(host('source_editor').locator('[data-webui-field-id="content"] textarea')).toHaveValue(/<html/)
  await closeDialog()
  await host('source_files').getByRole('button', { name: 'Загрузить исходный файл', exact: true }).click()
  for (const [field, value] of Object.entries({ title: 'Заметки для ревью', folder: 'originals/review', note: 'Использовать при подготовке текста' })) {
    await host('source_editor').locator(`[data-webui-field-id="${field}"] input, [data-webui-field-id="${field}"] textarea`).fill(value)
  }
  await host('source_editor').locator('[data-webui-field-id="role"] input[type="radio"]').first().check()
  await host('source_editor').locator('[data-webui-field-id="file_ref"] input[type="file"]').setInputFiles({
    name: 'review-note.md', mimeType: 'text/markdown', buffer: Buffer.from('Source material for isolated UI review.\n'),
  })
  await expect(host('source_editor').locator('[data-command-id="create"]')).toBeEnabled()
  await host('source_editor').locator('[data-command-id="create"]').click()
  await closedDialog()
  await expect(host('source_files')).toContainText('Заметки для ревью')
  await screenshot('prototype-source-materials.png')
  await host('source_files').getByText('Заметки для ревью', { exact: true }).click()
  await host('source_editor').locator('[data-webui-field-id="note"] textarea').fill('Уточнённая инструкция использования')
  await host('source_editor').locator('[data-command-id="update"]').click()
  await closedDialog()
  record('private-source-material-upload-view-and-revision-guarded-update')

  await tool('chat')
  const selectedContext = await host('chat_context').textContent()
  await composer.fill('Используй @Заметки')
  await expect(page.getByRole('dialog', { name: 'Выбор материала', exact: true })).toBeVisible()
  await composer.press('Enter')
  await expect(composer).toHaveValue(/@\[Заметки для ревью\]/)
  await expect(composer).not.toHaveValue(/adaos-resource:/)
  await host('site_discussion').getByRole('button', { name: 'Добавить файлы и инструменты' }).click()
  await expect(page.locator('.tool-menu')).not.toContainText('Прикрепить файлы')
  await page.locator('.tool-menu [role="menuitem"]').filter({ hasText: 'Вставить ссылку на материал' }).click()
  await page.getByRole('searchbox', { name: 'Найти материал' }).fill('Прототип')
  await page.getByRole('option').filter({ hasText: 'Прототип AdaOS.html' }).click()
  await host('site_discussion').getByRole('button', { name: 'Добавить файлы и инструменты' }).click()
  await page.locator('.tool-menu [role="menuitem"]').filter({ hasText: 'Вставить ссылку на материал' }).click()
  const mediaOption = page.getByRole('option').filter({ has: page.locator('small').filter({ hasText: /^(image|video)$/ }) }).first()
  const mediaLabel = await mediaOption.locator('span').textContent()
  await mediaOption.click()
  await host('site_discussion').getByRole('button', { name: 'Добавить файлы и инструменты' }).click()
  await page.locator('.tool-menu [role="menuitemcheckbox"]').click()
  await expect(host('site_discussion').getByRole('button', { name: 'Выключить веб-поиск' })).toBeVisible()
  await expect(host('chat_context')).toHaveText(selectedContext)
  await screenshot('prototype-chat-materials.png')
  await host('site_discussion').getByRole('button', { name: 'Отправить сообщение', exact: true }).click()
  await expect(host('site_discussion').locator('.material-reference')).toHaveCount(3)
  const outgoing = await host('site_discussion').locator('ada-chat-widget').evaluate(element => {
    const component = window.ng.getComponent(element)
    const state = component.pageState.getSnapshot()
    return { selected: state.selected_sections_id, section: state.review_last_context.section_id,
      anchor: state.review_last_context.source_anchor, refs: state.review_last_references.map(ref => ({ id: ref.id, uri: ref.uri, kind: ref.kind })),
      webSearch: state.review_last_web_search }
  })
  expect(outgoing.section).toBe(outgoing.selected)
  expect(outgoing.anchor).toContain(outgoing.section)
  expect(outgoing.refs).toHaveLength(3)
  expect(outgoing.webSearch).toBe(true)
  report.outgoingContext = outgoing
  await host('site_discussion').locator('.material-reference').filter({ hasText: 'Заметки для ревью' }).click()
  await expect(host('source_editor').locator('[data-webui-field-id="note"] textarea')).toHaveValue('Уточнённая инструкция использования')
  await closeDialog()
  await host('site_discussion').locator('.messages').getByRole('button', { name: mediaLabel, exact: true }).first().click()
  await expect(host('e_assets')).toBeVisible()
  await closeDialog()
  record('shared-composer-plus-and-mention-references-complement-block-context')

  await composer.fill('Снова используй @Заметки')
  await composer.press('Enter')
  await tool('materials')
  await host('source_files').getByText('Заметки для ревью', { exact: true }).click()
  await host('source_editor').locator('[data-command-id="delete"]').click()
  await page.locator('ion-alert:visible').getByRole('button').last().click()
  await closedDialog()
  await expect(host('source_files')).not.toContainText('Заметки для ревью')
  await tool('chat')
  await expect(host('site_discussion').getByRole('alert')).toContainText('Материал изменён или недоступен')
  await expect(host('site_discussion').getByRole('button', { name: 'Отправить сообщение', exact: true })).toBeDisabled()
  await composer.fill('Черновик сообщения для выбранного раздела')
  await expect(host('site_discussion').getByRole('alert')).toHaveCount(0)
  record('deleted-source-reference-blocks-send-without-losing-draft')

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
  let preparePublication = host('publication_commands').locator('[data-command-id="prepare"]')
  if (!await preparePublication.count()) {
    await host('publication_commands').locator('.auto-overflow__more').click()
    preparePublication = page.locator('ion-popover:visible [data-command-id="prepare"]')
    await expect(preparePublication).toBeDisabled()
    await page.keyboard.press('Escape')
    await expect(page.locator('ion-popover:visible')).toHaveCount(0)
  } else await expect(preparePublication).toBeDisabled()
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
  expect(report.sourceErrors).toEqual([])
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
