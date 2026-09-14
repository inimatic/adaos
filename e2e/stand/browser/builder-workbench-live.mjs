import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'

const hub = process.env.ADAOS_E2E_HUB_URL
const token = process.env.ADAOS_E2E_HUB_TOKEN
const output = process.env.ADAOS_E2E_OUTPUT
if (!hub || !token || !output || process.env.ENV_TYPE !== 'dev') throw new Error('Explicit local DEV authorization required')
const inspectOnly = process.argv.includes('--inspect')
const exerciseIndex = process.argv.indexOf('--exercise-test')
const exerciseId = exerciseIndex >= 0 ? process.argv[exerciseIndex + 1] : null
const report = { checks: [], errors: [], captures: [], requests: [], passed: false, mode: inspectOnly ? 'inspect' : 'read-only-controls' }
const browser = await chromium.launch({ headless: true })
await fs.mkdir(output, { recursive: true })
try {
  for (const [profile, viewport] of [['wide', { width: 1440, height: 1000 }], ['compact', { width: 390, height: 844 }]]) {
    if (exerciseId && profile !== 'wide') continue
    const context = await browser.newContext({ viewport, locale: 'ru-RU', colorScheme: 'dark' })
    await context.addInitScript(({ hub, token }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({
        adaos_device_id: 'builder-live-review', adaos_webspace_id: 'desktop-dev', adaos_lang: 'ru',
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: 'sn_6acf0c01', adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo',
      })) localStorage.setItem(key, value)
    }, { hub, token })
    const page = await context.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', error => report.errors.push({ profile, error: error.message }))
    page.on('response', async response => {
      if (!/\/skills\/|\/tools\//.test(response.url())) return
      const request = response.request()
      report.requests.push({ profile, path: new URL(response.url()).pathname, status: response.status(),
        body: request.postDataJSON(), response: await response.json().catch(() => null) })
    })
    const widget = id => page.locator(`[data-webui-widget-id="${id}"]`).filter({ visible: true }).first()
    const command = id => page.locator(`[data-command-id="${id}"]`).filter({ visible: true }).first()
    const loaded = async (name, widgetId) => {
      await page.waitForFunction(({ id }) => {
        const container = document.querySelector(`[data-webui-widget-id="${id}"]`)
        return container && !/Loading|Загрузка/.test(container.textContent || '')
      }, { id: widgetId }, { timeout: 30000 })
      const entry = report.requests.find(row => row.profile === profile && row.body?.tool === `builder_sdk_control_skill:${name}`)
      if (!entry || entry.response?.ok !== true) throw new Error(`No successful live response for ${name}`)
    }
    const capture = async name => {
      const body = await page.locator('body').innerText()
      await page.screenshot({ path: path.join(output, `${profile}-${name}.png`), fullPage: true, animations: 'disabled' })
      const geometry = await page.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth,
        widgets: [...document.querySelectorAll('[data-webui-widget-id]')].filter(el => el.getBoundingClientRect().width)
          .map(el => ({ id: el.getAttribute('data-webui-widget-id'), x: el.getBoundingClientRect().x, width: el.getBoundingClientRect().width })) }))
      const stateDiagnostic = await page.evaluate(() => [...document.querySelectorAll('ada-details-widget')].map(el => {
        const instance = window.ng?.getComponent(el)
        return { id: instance?.widget?.id, state: instance?.state?.getSnapshot?.(),
          inputs: instance?.widget?.inputs, dataSource: instance?.widget?.dataSource }
      }))
      report.captures.push({ profile, name, body, geometry, stateDiagnostic })
      console.log(JSON.stringify({ profile, name, width: geometry.width }))
      if (geometry.width > geometry.viewport + 1) throw new Error(`Document overflow: ${name}`)
      if (/runtime is out of sync|source reconnects|Data source failed|Unknown skill tool/i.test(body)) throw new Error(`Data source failure: ${name}`)
      if (/P17 \/ V17|P16 \/ V16/.test(body)) report.errors.push({ profile, error: `Design fixture in live conversation: ${name}` })
    }
    const closeModal = async () => {
      const modal = page.locator('ion-modal').filter({ visible: true }).last()
      await modal.getByRole('button', { name: /^(close|закрыть)$/i }).click()
      await modal.waitFor({ state: 'hidden' })
    }
    try {
      const initialResponse = page.waitForResponse(response => response.request().postData()?.includes(':get_workbench') && response.status() === 200, { timeout: 60000 })
      await page.goto('http://127.0.0.1:8100/?intent=webspace.open&zone=lo&subnet_id=sn_6acf0c01&webspace_id=desktop-dev&space_kind=development&expected_scenario_id=builder&try_local_hub=1', { waitUntil: 'domcontentloaded', timeout: 60000 })
      await widget('design-current-work').waitFor({ timeout: 60000 })
      if ((await (await initialResponse).json()).ok !== true) throw new Error('Initial workbench read failed')
      report.captures.push({ profile, name: 'chat-source-diagnostic', value: await page.evaluate(() => {
        const container = document.querySelector('[data-webui-widget-id="design-conversation-side-task"]')
        for (const el of [container, ...container.querySelectorAll('*')]) {
          const component = window.ng?.getComponent(el)
          if (!component?.messages) continue
          return { widget: component.widget, history: component.historyConversationId, thread: component.historyThreadId,
            receiver: component.ydoc?.toJSON(component.ydoc.getPath('data/webio/receivers'))?.['voice_chat.messages'],
            messageIds: component.messages.map(row => row.id),
            state: component.pageState?.snapshot?.() }
        }
        return { angularDebugUnavailable: true }
      }) })
      await capture('initial')
      report.checks.push({ profile, check: 'live_workbench_render', passed: true })
      if (!inspectOnly) {
        await command('applications').click()
        await widget('project-picker-table').waitFor()
        await widget('project-picker-sample').waitFor()
        await widget('project-picker-archived').waitFor()
        await widget('project-picker-table').getByText('Builder', { exact: true }).waitFor({ timeout: 30000 })
        await capture('picker')
        report.checks.push({ profile, check: 'retained_table_and_filters', passed: true })
        await closeModal()
        if (exerciseId && profile === 'wide') {
          const { exerciseCreation } = await import('./builder-workbench-exercise.mjs')
          await exerciseCreation({ page, widget, command, capture, closeModal, report, id: exerciseId })
          if (process.env.ADAOS_E2E_OPEN_PREVIEW === '1') {
            const opened = page.waitForEvent('popup', { timeout: 60000 })
            const previewResponse = page.waitForResponse(response => response.request().postData()?.includes(':open_preview') && response.status() === 200,
              { timeout: 60000 })
            await command('open').click()
            const receipt = await (await previewResponse).json()
            if (!receipt.ok || receipt.result?.ok === false) throw new Error('Preview command failed')
            const previewPage = await opened
            await previewPage.waitForLoadState('domcontentloaded')
            report.preview = { url: previewPage.url(), response: receipt }
            await fs.writeFile(path.join(output, 'preview.json'), JSON.stringify(report.preview, null, 2) + '\n', 'utf8')
            await previewPage.close()
            report.checks.push({ profile, check: 'preview_opened_through_builder', passed: true })
          }
          continue
        }
        await command('settings').click()
        await widget('node-overview').waitFor()
        await widget('design-codex-settings').waitFor()
        await widget('design-codex-settings').locator('option').filter({ hasText: 'GPT-5.4' }).waitFor({ state: 'attached', timeout: 30000 })
        await loaded('get_model_settings', 'design-effective-models')
        await capture('settings')
        await closeModal()
        await command('view-profile').click()
        await page.locator('[data-command-option="detailed"]').filter({ visible: true }).click()
        for (const [section, expected] of [['brief', 'design-task-brief'], ['inputs', 'technical-spec-editor'], ['files', 'design-file-tree'], ['readme', 'design-readme'], ['development-feedback', 'development-feedback-list'], ['process', 'design-process'], ['checks', 'design-checks']]) {
          await command(section).click()
          await widget(expected).waitFor()
          const tool = { inputs: 'get_prompt_context', files: 'list_project_file_tree', readme: 'read_readme',
            'development-feedback': 'list_development_feedback', process: 'get_process_tree' }[section]
          if (tool) {
            await page.waitForResponse(response => response.request().postData()?.includes(`:${tool}`) && response.status() === 200, { timeout: 30000 })
              .catch(() => {})
            await loaded(tool, expected)
          }
          await capture(section)
          report.checks.push({ profile, check: `live_section:${section}`, passed: true })
        }
      }
    } catch (error) {
      console.log(JSON.stringify({ profile, error: error.message }))
      report.errors.push({ profile, error: error.message })
      await capture('blocker').catch(() => {})
    } finally {
      await context.close()
    }
  }
  report.passed = report.errors.length === 0
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
}
if (!report.passed) process.exitCode = 1
