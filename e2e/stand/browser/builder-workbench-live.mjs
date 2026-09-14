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
    if (process.env.ADAOS_E2E_PREPARE_TRIAL && profile !== 'wide') continue
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
    const waitForProjection = async id => {
      await page.waitForFunction(id => {
        const element = document.querySelector('[data-webui-widget-id="design-current-work"] ada-details-widget')
        const snapshot = element && window.ng?.getComponent(element)?.state?.getSnapshot()
        return snapshot?.workbench?.object_id === id && snapshot.current?.phase
          && snapshot.applicationTitle === snapshot.workbench.title
      }, id, { timeout: 60000 })
      await widget('design-current-work').getByText(/Этап|Stage/).first().waitFor()
    }
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
      if (process.env.ADAOS_E2E_SELECT_CREATED) {
        const selected = JSON.parse(process.env.ADAOS_E2E_SELECT_CREATED)
        await command('applications').click()
        await widget('project-picker-table').locator('input').first().fill(selected.id)
        await widget('project-picker-table').getByText(selected.title, { exact: true }).click()
        await waitForProjection(selected.id)
        await widget('design-workbench-header').getByText(selected.title, { exact: true }).first().waitFor()
      } else {
        await waitForProjection('builder')
      }
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
      if (process.env.ADAOS_E2E_OPEN_TRIAL) {
        const trial = JSON.parse(process.env.ADAOS_E2E_OPEN_TRIAL)
        const trialCalls = []
        context.on('response', async response => {
          if (!response.request().postData()?.includes(`${trial.scenario}_skill:`)) return
          trialCalls.push({ status: response.status(), response: await response.json().catch(() => null) })
        })
        await command('specimens').click()
        const node = widget('process-tree').locator('.tree-widget__node').filter({ hasText: 'Beta in desktop-dev-dev' })
        await node.waitFor({ timeout: 30000 })
        const response = page.waitForResponse(response => response.request().postData()?.includes('builder_sdk_control_skill:get_project_placement_navigation'), { timeout: 90000 })
        void response.catch(() => {})
        const opened = page.waitForEvent('popup', { timeout: 90000 })
        void opened.catch(() => {})
        await node.getByRole('button', { name: /open placement/i }).click()
        const value = await (await response).json()
        await fs.writeFile(path.join(output, `${profile}-trial-navigation.json`), JSON.stringify(value, null, 2) + '\n', 'utf8')
        if (!value.ok || value.result?.placement?.result_ref?.id !== trial.delivery.candidate_id) throw new Error('Trial navigation identity mismatch')
        const preview = await opened
        await preview.locator('[data-webui-widget-id="books_list"]').waitFor({ timeout: 60000 })
        await preview.getByText(/The selected Trial runtime is not available|Среда исполнения выбранного Trial недоступна/).first().waitFor({ timeout: 60000 })
        if (!trialCalls.length || trialCalls.some(call => call.status !== 409 || call.response?.detail?.error !== 'trial_runtime_unavailable')) {
          throw new Error('Trial must deny unavailable execution instead of reading DEV')
        }
        await fs.writeFile(path.join(output, `${profile}-trial-execution.json`), JSON.stringify(trialCalls, null, 2) + '\n', 'utf8')
        await preview.screenshot({ path: path.join(output, `${profile}-trial.png`), fullPage: true, animations: 'disabled' })
        report.checks.push({ profile, check: 'exact_trial_opened_from_process', passed: true, url: preview.url() })
        report.checks.push({ profile, check: 'trial_execution_explicitly_unavailable_not_accepted', passed: true })
        await preview.close()
      }
      if (process.env.ADAOS_E2E_PREPARE_TRIAL) {
        const admission = JSON.parse(process.env.ADAOS_E2E_PREPARE_TRIAL)
        const current = await widget('design-current-work').locator('ada-details-widget')
          .evaluate(el => window.ng.getComponent(el).state.getSnapshot())
        if (current.workbench.automation_task_id !== admission.task || current.selectedProjectId !== admission.scenario
          || current.workbench.delivery.status !== 'checkpoint') throw new Error('Exact completed TEST checkpoint required')
        await fs.writeFile(path.join(output, 'trial-admission.json'), JSON.stringify(admission, null, 2) + '\n', { encoding: 'utf8', flag: 'wx' })
        await command('publication').click()
        await widget('publication-actions').waitFor()
        await capture('trial-ready')
        const pending = page.waitForResponse(response => response.request().postData()?.includes('builder_sdk_control_skill:publish_project'), { timeout: 180000 })
        void pending.catch(() => {})
        await widget('publication-actions').locator('[data-command-id="dry-run"]').click()
        let response = await pending
        let result = await response.json()
        if (result.detail?.error === 'action_approval_required') {
          const actionId = result.detail.pending_action_id
          if (!actionId || result.detail.tool !== 'builder_sdk_control_skill:publish_project') throw new Error('Unexpected approval target')
          await fs.writeFile(path.join(output, 'trial-network-approval.json'), JSON.stringify(result, null, 2) + '\n', 'utf8')
          await closeModal()
          if (!await page.locator('.pending-action-item').count()) await page.locator('.pending-actions-fab').click()
          await page.waitForFunction(actionId => [...document.querySelectorAll('.pending-action-item')]
            .some(el => window.ng?.getContext(el)?.$implicit?.id === actionId), actionId, { timeout: 30000 })
          const index = await page.locator('.pending-action-item').evaluateAll((items, actionId) =>
            items.findIndex(el => window.ng?.getContext(el)?.$implicit?.id === actionId), actionId)
          if (index < 0) throw new Error('Exact owned approval is not visible')
          await capture('network-approval')
          await page.locator('.pending-action-item').nth(index).getByRole('button', { name: /^(подтвердить|approve)(:|$)/i }).click()
          await page.waitForFunction(actionId => ![...document.querySelectorAll('.pending-action-item')]
            .some(el => window.ng?.getContext(el)?.$implicit?.id === actionId), actionId, { timeout: 30000 })
          await command('publication').click()
          const approvedResponse = page.waitForResponse(response => response.request().postData()?.includes('builder_sdk_control_skill:publish_project'), { timeout: 180000 })
          void approvedResponse.catch(() => {})
          await widget('publication-actions').locator('[data-command-id="dry-run"]').click()
          response = await approvedResponse
          result = await response.json()
        }
        await fs.writeFile(path.join(output, 'trial-result.json'), JSON.stringify(result, null, 2) + '\n', 'utf8')
        if (!response.ok() || result.ok !== true || result.result?.trial_ready !== true) throw new Error('Trial preparation failed; inspect the retained result before retry')
        report.checks.push({ profile, check: 'native_trial_preparation', passed: true })
        await capture('trial-created')
      }
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
        await widget('design-codex-settings').locator('option').filter({ hasText: 'GPT-5.5' }).waitFor({ state: 'attached', timeout: 30000 })
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
