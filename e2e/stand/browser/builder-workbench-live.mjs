import { chromium } from 'playwright'
import { expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const hub = process.env.ADAOS_E2E_HUB_URL
const token = process.env.ADAOS_E2E_HUB_TOKEN
const output = process.env.ADAOS_E2E_OUTPUT
if (!hub || !token || !output || process.env.ENV_TYPE !== 'dev') throw new Error('Explicit local DEV authorization required')
const inspectOnly = process.argv.includes('--inspect')
const exerciseIndex = process.argv.indexOf('--exercise-test')
const exerciseId = exerciseIndex >= 0 ? process.argv[exerciseIndex + 1] : null
const locale = process.env.ADAOS_E2E_LOCALE === 'en' ? 'en' : 'ru'
const report = { checks: [], errors: [], captures: [], requests: [], passed: false, locale, mode: inspectOnly ? 'inspect' : 'read-only-controls' }
const browser = await chromium.launch({ headless: true })
await fs.mkdir(output, { recursive: true })
try {
  for (const [profile, viewport] of [['wide', { width: 1440, height: 1000 }], ['compact', { width: 390, height: 844 }]]) {
    if (exerciseId && profile !== 'wide') continue
    if (process.env.ADAOS_E2E_PREPARE_TRIAL && profile !== 'wide') continue
    if (process.env.ADAOS_E2E_ACCEPT_TRIAL && profile !== 'wide') continue
    if (process.env.ADAOS_E2E_OBSERVE_PROTOTYPE && profile !== 'wide') continue
    if (process.env.ADAOS_E2E_ANSWER_CLARIFICATION && profile !== 'wide') continue
    const context = await browser.newContext({ viewport, locale: locale === 'ru' ? 'ru-RU' : 'en-US', colorScheme: 'dark' })
    await context.addInitScript(({ hub, token, locale }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({
        adaos_device_id: 'builder-live-review', adaos_webspace_id: 'desktop-dev', adaos_lang: locale,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: 'sn_6acf0c01', adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo',
      })) localStorage.setItem(key, value)
    }, { hub, token, locale })
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
      const selectedReceipt = process.env.ADAOS_E2E_SELECT_CREATED || process.env.ADAOS_E2E_CREATED_TEST
      if (selectedReceipt) {
        const selected = JSON.parse(selectedReceipt)
        await command('applications').click()
        await widget('project-picker-table').locator('input').first().fill(selected.title)
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
      if (process.env.ADAOS_E2E_ABOUT) {
        const { reviewAbout } = await import('./builder-about-review.mjs')
        await reviewAbout({ page, widget, command, closeModal, capture, report, profile })
        continue
      }
      if (process.env.ADAOS_E2E_ANSWER_CLARIFICATION) {
        const { reviewClarification } = await import('./builder-clarification-review.mjs')
        await reviewClarification({ page, widget, capture, report, output,
          intent: JSON.parse(process.env.ADAOS_E2E_ANSWER_CLARIFICATION) })
        continue
      }
      if (process.env.ADAOS_E2E_OBSERVE_PROTOTYPE) {
        const { observePrototype } = await import('./builder-prototype-observation.mjs')
        await observePrototype({ chat: widget('design-conversation-side-task'),
          intent: JSON.parse(process.env.ADAOS_E2E_OBSERVE_PROTOTYPE), output, capture,
          record: (check, details) => report.checks.push({ profile, check, passed: true, ...details }) })
        continue
      }
      await command('specimens').click()
      const processMenu = page.locator('ion-popover').filter({ visible: true }).last()
      const stages = processMenu.getByRole('menuitemradio')
      await expect(stages).toHaveCount(6, { timeout: 60000 })
      await expect(processMenu.locator('[aria-current="step"]')).toHaveCount(1)
      await expect(stages.first()).toBeEnabled()
      await capture('process-menu')
      report.checks.push({ profile, check: 'live_process_menu_current_step', passed: true })
      await page.keyboard.press('Escape')
      if (process.env.ADAOS_E2E_VERIFY_EXISTING_PREVIEW) {
        const selected = JSON.parse(process.env.ADAOS_E2E_SELECT_CREATED)
        const response = page.waitForResponse(reply => reply.request().postData()?.includes(':open_preview'), { timeout: 90000 })
        const popup = page.waitForEvent('popup', { timeout: 90000 })
        void popup.catch(() => {})
        await command('open').click()
        const reply = await (await response).json()
        if (!reply.ok || !reply.result?.ok) throw new Error('Open Preview failed')
        const target = reply.result.navigation?.target
        if (target?.object_type !== 'project' || target?.object_id !== selected.id || !target?.revision) {
          throw new Error('Preview lost the selected Application or its revision')
        }
        const opened = await popup
        await opened.waitForLoadState('domcontentloaded')
        const url = new URL(opened.url())
        if (url.searchParams.get('expected_scenario_id') !== target.scenario_id
            || url.searchParams.get('expected_revision') !== target.revision) throw new Error('Preview URL identity mismatch')
        await expect(widget('design-revision-identity')).toContainText(target.label, { timeout: 30000 })
        await capture('preview-target')
        report.checks.push({ profile, check: 'existing_preview_result_matches_opened_revision', passed: true, target })
        await opened.close()
      }
      if (process.env.ADAOS_E2E_REFINEMENT) {
        await command('applications').click()
        const modal = page.locator('ion-modal').filter({ visible: true }).last()
        if (profile === 'wide') {
          const bounds = () => modal.evaluate(el => el.shadowRoot.querySelector('.modal-wrapper').getBoundingClientRect().width)
          const before = await bounds()
          if (before < viewport.width - 20) {
            await modal.getByRole('button', { name: 'Fullscreen', exact: true }).click()
            await expect.poll(bounds).toBeCloseTo(viewport.width, 0)
          }
          const fullWidth = await bounds()
          await modal.getByRole('button', { name: 'Fullscreen', exact: true }).click()
          await expect.poll(bounds).toBeLessThan(fullWidth - 20)
          const controls = modal.locator('ada-modal-size-controls')
          const width = controls.locator('input[type="range"]').first()
          await width.evaluate(el => { el.value = '70'; el.dispatchEvent(new Event('input', { bubbles: true })) })
          await controls.getByRole('button', { name: /^(Сохранить размер|Save size)$/ }).click()
          await expect(controls.getByRole('status')).toHaveText(/Размер сохранён|Size saved/)
          await capture('modal-size-saved')
          await closeModal()
          await command('applications').click()
          await expect.poll(bounds).toBeCloseTo(viewport.width * .7, 0)
          report.checks.push({ profile, check: 'modal_maximize_resize_reopen', passed: true })
          if (process.env.ADAOS_E2E_MODAL_SETTINGS_ONLY) {
            await modal.getByRole('button', { name: /Resize dialog|Размер окна/i }).first().click()
            await controls.locator('input[type="range"]').first().evaluate(el => { el.value = '72'; el.dispatchEvent(new Event('input', { bubbles: true })) })
            await controls.locator('select').nth(0).selectOption('all')
            const sharedReply = page.waitForResponse(response => response.request().method() === 'PATCH' && response.url().includes('/api/personalization/current-user/preferences'))
            await controls.getByRole('button', { name: /^(Сохранить размер|Save size)$/ }).click()
            const shared = await sharedReply
            const sharedBody = await shared.json()
            await fs.writeFile(path.join(output, 'shared-modal-preference.json'), JSON.stringify({ status: shared.status(), result: sharedBody }, null, 2) + '\n', 'utf8')
            if (!shared.ok()) throw new Error('Shared modal preference save failed: ' + JSON.stringify(sharedBody))
            await expect(controls.getByRole('status')).toHaveText(/Размер сохранён|Size saved/)
            await controls.locator('input[type="range"]').first().evaluate(el => { el.value = '74'; el.dispatchEvent(new Event('input', { bubbles: true })) })
            await controls.getByRole('button', { name: /Save in application|Сохранить в настройках приложения/i }).click()
            await expect(controls).toContainText(/webui.json/)
            const defaultReply = page.waitForResponse(response => response.request().method() === 'PATCH' && response.url().includes('/api/builder/modal-settings'))
            await controls.getByRole('button', { name: /Confirm DEV|Подтвердить.*DEV/i }).click()
            const saved = await defaultReply
            const body = await saved.json()
            await fs.writeFile(path.join(output, 'dev-modal-default.json'), JSON.stringify(body, null, 2) + '\n', 'utf8')
            if (!saved.ok() || body.ok !== true) throw new Error('Explicit DEV default edit failed')
            await capture('shared-and-dev-modal-settings')
            await closeModal()
            await command('applications').click()
            await expect.poll(bounds).toBeCloseTo(viewport.width * .72, 0)
            report.checks.push({ profile, check: 'current_user_shared_preference_and_explicit_dev_default', passed: true })
          }
        }
        if (profile === 'compact' && process.env.ADAOS_E2E_MODAL_SETTINGS_ONLY) {
          await expect.poll(() => modal.locator('ada-modal-size-controls').evaluate(el => window.ng.getComponent(el).size.width)).toBe(72)
          report.checks.push({ profile, check: 'shared_preference_loaded_in_new_browser_context', passed: true })
        }
        await closeModal()
        if (profile === 'wide' && !process.env.ADAOS_E2E_MODAL_SETTINGS_ONLY) {
          const details = await widget('design-current-work').locator('ada-details-widget')
            .evaluate(el => window.ng.getComponent(el).state.getSnapshot())
          if (!String(details.selectedProjectId).startsWith('workbench_test_')) throw new Error('README generation must target the retained TEST only')
          await command('inspect-section').click()
          await page.locator('[data-command-option="readme"]').filter({ visible: true }).click()
          await widget('design-readme-actions').getByRole('button', { name: /редакт|edit/i }).click()
          const form = widget('readme-generate')
          await form.locator('textarea').fill('Составь краткий README на русском для этого приложения: назначение и работа со списком чтения. Не добавляй неподтверждённые функции.')
          const responsePromise = page.waitForResponse(response => response.request().postData()?.includes('builder_sdk_control_skill:generate_readme'), { timeout: 90000 })
          await form.getByRole('button', { name: /Generate draft|Создать черновик/i }).click()
          let response = await (await responsePromise).json()
          await fs.writeFile(path.join(output, 'readme-submit.json'), JSON.stringify(response, null, 2) + '\n', 'utf8')
          if (response.detail?.error === 'action_approval_required') {
            const actionId = response.detail.pending_action_id
            if (!actionId || response.detail.tool !== 'builder_sdk_control_skill:generate_readme') throw new Error('Unexpected README approval target')
            await closeModal()
            if (!await page.locator('.pending-action-item').count()) await page.locator('.pending-actions-fab').click()
            await page.waitForFunction(actionId => [...document.querySelectorAll('.pending-action-item')]
              .some(el => window.ng?.getContext(el)?.$implicit?.id === actionId), actionId, { timeout: 30000 })
            const index = await page.locator('.pending-action-item').evaluateAll((items, actionId) =>
              items.findIndex(el => window.ng?.getContext(el)?.$implicit?.id === actionId), actionId)
            const resumed = page.waitForResponse(response => response.request().postData()?.includes('builder_sdk_control_skill:generate_readme'), { timeout: 90000 })
            await page.locator('.pending-action-item').nth(index).getByRole('button', { name: /^(подтвердить|approve)(:|$)/i }).click()
            response = await (await resumed).json()
            await fs.writeFile(path.join(output, 'readme-admitted.json'), JSON.stringify(response, null, 2) + '\n', 'utf8')
            await page.locator('.pending-actions-panel__header button').click()
            await widget('design-readme-actions').getByRole('button', { name: /редакт|edit/i }).click()
          }
          if (response.ok !== true) throw new Error('README submit not admitted; inspect the exact response before retry')
          await page.waitForFunction(() => {
            const element = document.querySelector('[data-webui-widget-id="readme-generation-status"] ada-details-widget')
            const status = element && window.ng?.getComponent(element)?.state?.getSnapshot()?.readmeGeneration?.status
            return ['completed', 'out_of_scope', 'refused', 'failed', 'cancelled', 'invalid_output', 'incomplete'].includes(status)
          }, null, { timeout: 600000 })
          await widget('readme-generated-draft').waitFor()
          await widget('readme-generated-draft').scrollIntoViewIfNeeded()
          await capture('readme-generated-draft')
          report.checks.push({ profile, check: 'readme_draft_generated_without_save', passed: true })
          const draft = widget('readme-generated-draft')
          const draftText = await draft.locator('textarea').inputValue()
          if (!draftText.trim()) throw new Error('Generated README is empty')
          const saveReply = page.waitForResponse(response => response.request().postData()?.includes('builder_sdk_control_skill:save_readme'), { timeout: 90000 })
          await draft.getByRole('button', { name: /Save|Сохранить/i }).click()
          const saved = await (await saveReply).json()
          await fs.writeFile(path.join(output, 'readme-save.json'), JSON.stringify({ response: saved, reviewed_text: draftText }, null, 2) + '\n', 'utf8')
          if (saved.ok !== true) throw new Error('Explicit README review/save failed')
          report.checks.push({ profile, check: 'readme_reviewed_and_saved', passed: true })
          await closeModal()
        }
      }
      if (process.env.ADAOS_E2E_OPEN_TRIAL) {
        const trial = JSON.parse(process.env.ADAOS_E2E_OPEN_TRIAL)
        const trialCalls = []
        context.on('response', async response => {
          if (!response.request().postData()?.includes(`${trial.scenario}_skill:`)) return
          trialCalls.push({ status: response.status(), headers: {
            source: response.headers()['x-adaos-runtime-source'],
            release: response.headers()['x-adaos-release-digest'] }, ok: (await response.json().catch(() => null))?.ok === true })
        })
        await command('inspect-section').click()
        await page.locator('ion-popover').filter({ visible: true }).last().locator('[data-command-option="process"]').click()
        const node = widget('design-process').locator(`ion-item[data-focus-ref="trial:${trial.delivery.candidate_id}"]`)
        await node.waitFor({ timeout: 30000 })
        const response = page.waitForResponse(response => response.request().postData()?.includes('builder_sdk_control_skill:get_project_placement_navigation'), { timeout: 90000 })
        void response.catch(() => {})
        const opened = page.waitForEvent('popup', { timeout: 90000 })
        void opened.catch(() => {})
        await node.getByRole('button', { name: 'Open placement', exact: true }).click()
        const value = await (await response).json()
        await fs.writeFile(path.join(output, `${profile}-trial-navigation.json`), JSON.stringify(value, null, 2) + '\n', 'utf8')
        if (!value.ok || value.result?.placement?.result_ref?.id !== trial.delivery.candidate_id) throw new Error('Trial navigation identity mismatch')
        const preview = await opened
        await preview.locator('[data-webui-widget-id="books_list"]').waitFor({ state: 'attached', timeout: 60000 })
        await expect.poll(() => trialCalls.length, { timeout: 60000 }).toBeGreaterThan(0)
        await fs.writeFile(path.join(output, `${profile}-trial-execution.json`), JSON.stringify({
          url: preview.url(), calls: trialCalls }, null, 2) + '\n', 'utf8')
        await preview.waitForFunction(() => {
          const list = document.querySelector('[data-webui-widget-id="books_list"]')
          return list && !/Loading|Загрузка/.test(list.textContent)
        }, null, { timeout: 60000 })
        if (!trialCalls.length || trialCalls.some(call => call.status !== 200 || call.ok !== true
          || call.headers.source !== 'trial' || call.headers.release !== trial.delivery.release_digest)) {
          throw new Error('Trial must execute its exact package in production')
        }
        await fs.writeFile(path.join(output, `${profile}-trial-execution.json`), JSON.stringify(trialCalls, null, 2) + '\n', 'utf8')
        report.checks.push({ profile, check: 'installed-records-not-exported', passed: true })
        report.checks.push({ profile, check: 'exact_trial_opened_from_process', passed: true, url: preview.url() })
        if (new URL(preview.url()).searchParams.get('webspace_id') !== trial.placement.target.webspace_id) throw new Error('Trial opened in wrong Webspace')
        report.checks.push({ profile, check: 'exact_trial_execution_from_builder_placement', passed: true })
        const chrome = await preview.evaluate(() => {
          const app = window.ng?.getComponent(document.querySelector('app-root'))
          const projection = value => ({ component: value?.component, version: value?.version,
            source: value?.sourceAuthority, stage: value?.releaseStage,
            updateStage: value?.componentUpdate?.stage, candidate: value?.componentUpdate?.candidate?.id })
          const item = app?.findScenarioCatalogItem?.(app.currentScenario)
          return { scenario: app?.currentScenario, ownerChrome: app?.showOwnerChrome,
            badge: { id: app?.currentScenarioBadge?.id, stage: app?.currentScenarioBadge?.releaseStage },
            catalog: projection(item?._adaos), page: projection(app?.currentRuntimePageMetadata?.()),
            catalogStage: item?.release_stage, changelogPresent: !!document.querySelector('.scenario-changelog-btn') }
        })
        await fs.writeFile(path.join(output, `${profile}-trial-chrome.json`), JSON.stringify(chrome, null, 2) + '\n', 'utf8')
        if (process.env.ADAOS_E2E_ACCEPT_TRIAL) {
          await preview.locator('.scenario-changelog-btn').click()
          const panel = preview.locator('.component-updates-panel')
          await panel.locator('.component-update-detail').waitFor()
          const reviewed = await panel.evaluate(el => window.ng.getOwningComponent(el).selectedComponentUpdate)
          if (reviewed?.component?.id !== trial.scenario || reviewed?.source_kind !== 'builder_local_trial'
            || reviewed?.candidate?.id !== trial.delivery.candidate_id
            || reviewed?.candidate?.digest !== trial.placement.result_ref.digest) throw new Error('Changelog Candidate differs from the admitted Trial')
          await fs.writeFile(path.join(output, 'reviewed-local-candidate.json'), JSON.stringify(reviewed, null, 2) + '\n', 'utf8')
          await panel.screenshot({ path: path.join(output, 'beta-changelog.png') })
          const accepted = preview.waitForResponse(response => new URL(response.url()).pathname.endsWith('/accept-trial'), { timeout: 600000 })
          await panel.getByRole('button', { name: /Accept into Workspace|Принять в Workspace/i }).click()
          const reply = await accepted
          const result = await reply.json()
          await fs.writeFile(path.join(output, 'workspace-acceptance.json'), JSON.stringify({ status: reply.status(), result }, null, 2) + '\n', 'utf8')
          if (!reply.ok() || result.ok !== true || result.runtime_selection?.source !== 'stable_installation') throw new Error('Workspace acceptance not confirmed')
          await panel.locator('.component-update-state[data-stage="stable"]').waitFor({ timeout: 60000 })
          await panel.screenshot({ path: path.join(output, 'workspace-changelog.png') })
          await panel.locator('.component-updates-panel__tools button').last().click()
          const stableRead = preview.waitForResponse(response => response.request().postData()?.includes(`${trial.scenario}_skill:`)
            && response.headers()['x-adaos-runtime-source'] !== 'trial', { timeout: 60000 })
          await preview.reload({ waitUntil: 'domcontentloaded' })
          const stableReply = await stableRead
          if (!stableReply.ok() || (await stableReply.json()).ok !== true) throw new Error('Workspace execution failed after reload')
          await preview.locator('[data-webui-widget-id="books_list"]').waitFor({ state: 'attached', timeout: 60000 })
          await expect(preview.locator('.scenario-changelog-btn')).toHaveCount(0)
          await preview.setViewportSize({ width: 390, height: 844 })
          const fits = await preview.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)
          if (!fits) throw new Error('Workspace compact layout overflows')
          report.checks.push({ profile, check: 'beta_changelog_workspace_acceptance_and_reload', passed: true })
        }
        await preview.close()
      }
      if (process.env.ADAOS_E2E_PREPARE_TRIAL) {
        const admission = JSON.parse(process.env.ADAOS_E2E_PREPARE_TRIAL)
        const current = await widget('design-current-work').locator('ada-details-widget')
          .evaluate(el => window.ng.getComponent(el).state.getSnapshot())
        const exactDelivery = admission.resume_candidate_id
          ? current.workbench.delivery.status === 'trial' && current.workbench.delivery.candidate_id === admission.resume_candidate_id
          : current.workbench.delivery.status === 'checkpoint'
        if (current.workbench.automation_task_id !== admission.task || current.selectedProjectId !== admission.scenario
          || !exactDelivery) throw new Error('Exact completed TEST checkpoint or admitted interrupted Trial required')
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
        const retainedTitle = selectedReceipt ? JSON.parse(selectedReceipt).title : 'Builder'
        await widget('project-picker-table').locator('input').first().fill(retainedTitle)
        await widget('project-picker-table').getByText(retainedTitle, { exact: true }).waitFor({ timeout: 30000 })
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
          const tool = { inputs: 'get_prompt_context', files: 'list_project_file_tree', readme: 'get_about',
            'development-feedback': 'list_development_feedback', process: 'get_process_stages' }[section]
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
