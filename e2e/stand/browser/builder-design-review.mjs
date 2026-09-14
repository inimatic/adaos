import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'

const workspaceOnly = process.argv.includes('--workspace-inspect')
const webspace = workspaceOnly ? 'desktop' : 'desktop-dev'
const hub = process.env.ADAOS_E2E_HUB_URL
const token = process.env.ADAOS_E2E_HUB_TOKEN
const specimen = JSON.parse(await fs.readFile('.adaos/dev/sn_6acf0c01/scenarios/builder/webui.json', 'utf8'))
const revision = specimen.ui.version
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || `e2e/artifacts/builder/workbench-design-20260914/${workspaceOnly ? 'workspace' : `browser-${revision}`}`)
const samples = specimen.ui.application.desktop.pageSchema.initialState.samples
if (!hub || !token || process.env.ENV_TYPE !== 'dev') throw new Error('Explicit local DEV authorization is required')
const browser = await chromium.launch({ headless: true })
const report = { checks: [], errors: [], pages: [], passed: false }
await fs.mkdir(output, { recursive: true })
try {
  for (const [profile, viewport] of [['wide', { width: 1440, height: 1000 }], ['compact', { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport, locale: 'ru-RU', colorScheme: 'dark' })
    await context.addInitScript(({ hub, token, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      window.__builderDesignFeedbackEvents = []
      window.addEventListener('adaos:openDevTickets', event => window.__builderDesignFeedbackEvents.push(event.detail))
      for (const [key, value] of Object.entries({
        adaos_device_id: 'builder-design-review', adaos_webspace_id: webspace, adaos_lang: 'ru',
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: 'sn_6acf0c01', adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo',
      })) localStorage.setItem(key, value)
    }, { hub, token, webspace })
    const page = await context.newPage()
    page.setDefaultTimeout(10000)
    page.on('pageerror', error => report.errors.push({ profile, error: error.message }))
    const url = `http://127.0.0.1:8100/?intent=webspace.open&zone=lo&subnet_id=sn_6acf0c01&webspace_id=${webspace}&space_kind=${workspaceOnly ? 'workspace' : 'development'}&expected_scenario_id=builder&try_local_hub=1`
    const command = id => page.locator(`[data-command-id="${id}"]`).filter({ visible: true }).first()
    const capture = async name => {
      await page.evaluate(async () => {
        for (const el of document.querySelectorAll('ion-content')) await el.scrollToTop(0)
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      })
      await page.screenshot({ path: path.join(output, `${profile}-${name}.png`), fullPage: true, animations: 'disabled' })
      const geometry = await page.evaluate(() => ({
        viewport: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        widgets: [...document.querySelectorAll('[data-webui-widget-id]')].filter(el => el.getBoundingClientRect().width).map(el => ({
          id: el.getAttribute('data-webui-widget-id'), x: el.getBoundingClientRect().x, width: el.getBoundingClientRect().width,
        })),
      }))
      report.pages.push({ profile, name, geometry })
      console.log(JSON.stringify({ profile, name, documentWidth: geometry.documentWidth }))
    }
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
      if (workspaceOnly) {
        await page.locator('[data-webui-widget-id="project-header"]').waitFor({ timeout: 60000 })
        await page.getByRole('button', { name: /choose project|выбрать проект/i }).waitFor({ timeout: 30000 })
        if (await command('specimens').count()) throw new Error('Design specimen replaced Workspace Builder')
        await capture('workspace-preserved')
        report.checks.push({ profile, check: 'workspace_operational_surface_preserved', passed: true })
      } else {
      await command('specimens').waitFor({ timeout: 60000 })
      await page.getByText(/^Builder design prototype/).first().waitFor({ state: 'attached', timeout: 30000 })
      await capture('initial')
      report.checks.push({ profile, check: 'render', passed: true, body: (await page.locator('body').innerText()).slice(0, 12000) })
      if (!process.argv.includes('--inspect')) {
        const selectState = async id => {
          await command('specimens').click()
          await page.locator(`[data-command-option="${id}"]`).filter({ visible: true }).click()
          await page.locator('[data-webui-widget-id="design-workbench-header"] .control-status strong').filter({ hasText: samples[id].title }).waitFor()
        }
        await command('specimens').press('ArrowDown')
        const menu = page.getByRole('menu').filter({ visible: true })
        await menu.waitFor()
        await capture('process-menu')
        const menuGeometry = await menu.evaluate(el => {
          const rect = el.getBoundingClientRect()
          const point = document.elementFromPoint(rect.left + 20, rect.top + 25)
          return { onTop: el.contains(point), left: rect.left, right: rect.right, viewport: innerWidth, focused: el.contains(document.activeElement) }
        })
        if (!menuGeometry.onTop || !menuGeometry.focused || menuGeometry.left < 0 || menuGeometry.right > viewport.width) throw new Error(`Menu overlay/focus failed: ${JSON.stringify(menuGeometry)}`)
        await page.keyboard.press('Escape')
        await menu.waitFor({ state: 'hidden' })
        await page.waitForFunction(() => document.activeElement?.getAttribute('data-command-id') === 'specimens')
        report.checks.push({ profile, check: 'menu_overlay_keyboard_and_bounded_width', passed: true })
        await command('accept').click()
        const acceptance = page.locator('ion-modal').last()
        await acceptance.getByRole('button', { name: 'Принять 003 в макете', exact: true }).click()
        await command('implement').waitFor()
        report.checks.push({ profile, check: 'accept_does_not_start_implementation', passed: true })
        await selectState('input')
        await command('answer').click()
        const question = page.locator('ion-modal').last()
        await question.locator('select').selectOption({ label: 'Telegram' })
        await command('save-draft').click()
        await command('answer').waitFor()
        await command('answer').click()
        if (!(await page.locator('ion-modal select option:checked').innerText()).includes('Telegram')) throw new Error('Clarification draft was lost')
        await page.locator('ion-modal').last().getByRole('button', { name: 'Ответить и продолжить в макете', exact: true }).click()
        if (!await page.locator('ion-modal').last().isVisible() || await command('stop').count()) throw new Error('An incomplete answer resumed execution')
        await page.locator('ion-modal').last().locator('input').fill('maintenance@example.test')
        await capture('batch-clarification')
        await question.getByRole('button', { name: 'Ответить и продолжить в макете', exact: true }).click()
        await command('stop').waitFor()
        report.checks.push({ profile, check: 'clarification', passed: true })
        await command('stop').click()
        await command('confirm').click()
        await command('inspect').waitFor()
        report.checks.push({ profile, check: 'acknowledged_stop', passed: true })
        for (const id of Object.keys(samples)) {
          await selectState(id)
          await capture(id)
          if (['partial', 'failed', 'offline'].includes(id) && await command('accept').count()) throw new Error(`${id} exposes acceptance`)
        }
        await selectState('review')
        for (const id of ['brief', 'checks', 'process', 'conversation', 'inputs', 'files', 'deliveries', 'result']) {
          await command(id).click()
          await capture(`view-${id}`)
        }
        await command('history').click()
        await page.locator('ion-modal ada-table-widget tbody tr').filter({ hasText: '002' }).last().click()
        await capture('history')
        await command('show').click()
        await page.getByText('Прототип 002', { exact: true }).waitFor()
        if (await command('accept').count() || await command('edit').count()) throw new Error('Historical preview exposes candidate editing/acceptance')
        await command('inspect').click()
        await page.getByText('Прототип 003', { exact: true }).waitFor()
        report.checks.push({ profile, check: 'explicit_revision_navigation', passed: true })
        await page.locator('[data-webui-widget-id="design-assets"] tbody tr').filter({ hasText: 'Вентилятор' }).click()
        await command('edit').click()
        const editor = page.locator('ion-modal').last()
        if (await editor.locator('input').first().inputValue() !== 'Вентилятор Н-7') throw new Error('Editor did not load the selected record')
        if (await editor.locator('input').nth(1).inputValue() !== 'Котельная') throw new Error('Editor location is not initialized')
        await editor.locator('input').first().fill('Вентилятор Н-8')
        await editor.getByRole('button', { name: 'Сохранить в макете', exact: true }).click()
        await page.locator('[data-webui-widget-id="design-assets"]').getByText('Вентилятор Н-8', { exact: true }).waitFor()
        await command('edit').click()
        if (await page.locator('ion-modal').last().locator('input').first().inputValue() !== 'Вентилятор Н-8') throw new Error('Saved asset form is stale')
        await page.locator('ion-modal').last().getByRole('button', { name: /^(close|закрыть)$/i }).click()
        report.checks.push({ profile, check: 'specimen_edit_updates_table_and_form', passed: true })
        await command('settings').click()
        await page.locator('ion-modal').last().getByRole('checkbox').check()
        await page.locator('ion-modal').last().getByRole('button', { name: 'Сохранить в макете', exact: true }).click()
        await page.getByText('Диагностика · образец', { exact: true }).waitFor()
        report.checks.push({ profile, check: 'settings_change_visible_diagnostics', passed: true })
        await command('settings').click()
        await page.locator('ion-modal').last().getByRole('checkbox').uncheck()
        await page.locator('ion-modal').last().getByRole('button', { name: 'Сохранить в макете', exact: true }).click()
        await page.locator('[data-webui-widget-id="design-diagnostics"]').waitFor({ state: 'hidden' })
        await command('files').click()
        await page.locator('[data-webui-widget-id="design-file-tree"]').getByText('webui.json', { exact: true }).click()
        await page.locator('ion-modal').last().getByText('scenarios/equipment/webui.json', { exact: true }).waitFor()
        if (await page.locator('ion-modal').last().locator('textarea,input').count()) throw new Error('File viewer is editable')
        await capture('file-viewer')
        await command('request-file-change').click()
        await page.locator('[data-webui-widget-id="design-file-request-scope"]').waitFor()
        report.checks.push({ profile, check: 'hierarchical_readonly_files_scoped_request', passed: true })
        await command('files').click()
        const gap = await page.evaluate(() => {
          const tabs = document.querySelector('[data-webui-widget-id="design-workbench-views"]').getBoundingClientRect()
          const files = document.querySelector('[data-webui-widget-id="design-files-scope"]').getBoundingClientRect()
          return files.top - tabs.bottom
        })
        if (gap > 48) throw new Error(`Hidden widgets still reserve layout gaps: ${gap}`)
        report.checks.push({ profile, check: 'conditional_hosts_do_not_reserve_layout', passed: true, gap })
        await command('inputs').click()
        await page.locator('[data-webui-widget-id="design-attach-form"] input[type=file]').setInputFiles({ name: 'reference.py', mimeType: 'text/plain', buffer: Buffer.from('print("reference only")') })
        await page.locator('[data-webui-widget-id="design-input-delivery"]').getByText(/reference.py/).waitFor()
        report.checks.push({ profile, check: 'input_metadata_selection_without_upload', passed: true })
        await page.locator('[data-webui-widget-id="design-reference-table"]').getByText('builder-reference.png', { exact: true }).click()
        await page.locator('ion-modal ada-media-preview [data-media-state="ready"]').waitFor()
        const pixels = await page.locator('ion-modal ada-media-preview img').evaluate(img => ({ width: img.naturalWidth, height: img.naturalHeight }))
        if (!pixels.width || !pixels.height) throw new Error('Reference screenshot did not load')
        await capture('reference-image')
        await page.locator('ion-modal').last().getByRole('button', { name: /^(close|закрыть)$/i }).click()
        report.checks.push({ profile, check: 'versioned_reference_image_rendered', passed: true, pixels })
        await selectState('implementation_review')
        await command('accept-implementation').click()
        await command('confirm-implementation').click()
        await page.getByText('Принято в DEV', { exact: true }).waitFor()
        report.checks.push({ profile, check: 'implementation_acceptance_is_separate', passed: true })
        for (const action of ['prepare-beta', 'install-beta', 'accept-beta', 'release-stable', 'publish-stable']) {
          await command(action).click()
          await capture(action)
          await command(`confirm-${action}`).click()
        }
        await page.getByText('Опубликовано', { exact: true }).waitFor()
        report.checks.push({ profile, check: 'explicit_beta_stable_publication_decisions_simulated', passed: true })
        await selectState('review')
        await command('feedback').click()
        await page.getByRole('button', { name: /screenshot|скриншот/i }).first().waitFor()
        const feedback = await page.evaluate(() => window.__builderDesignFeedbackEvents.at(-1))
        if (feedback?.target_scope?.source !== 'dev' || feedback?.target_scope?.id !== 'builder' || feedback?.target_scope?.revision !== revision) throw new Error('Feedback identity is missing or targets the wrong source')
        await capture('dev-tickets')
        report.checks.push({ profile, check: 'existing_dev_tickets_screenshot_entry', passed: true, feedback })
      }
      }
    } catch (error) {
      report.checks.push({ profile, passed: false, error: error.message, body: (await page.locator('body').innerText()).slice(0, 16000) })
      await capture('failure')
    }
    await context.close()
  }
  report.passed = report.checks.every(check => check.passed) && !report.errors.length
    && report.pages.every(page => page.geometry.documentWidth <= page.geometry.viewport)
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
}
console.log(JSON.stringify({ passed: report.passed, checks: report.checks.map(({ body, ...check }) => check), errors: report.errors }))
if (!report.passed) process.exitCode = 1
