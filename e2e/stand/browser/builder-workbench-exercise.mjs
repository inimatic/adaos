import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'

// Only this explicitly created TEST application may be changed by the review.
export async function exerciseCreation({ page, widget, command, capture, closeModal, report, id }) {
  assert.match(id, /^workbench_test_[a-z0-9_]+$/)
  const title = `Reading List [TEST]-20260914-${id.slice('workbench_test_'.length)}`
  const responseFor = tool => page.waitForResponse(response =>
    response.request().postData()?.includes(`builder_sdk_control_skill:${tool}`) && response.status() === 200,
    { timeout: 60000 })
  const success = async pending => {
    const value = await (await pending).json()
    assert.equal(value.ok, true, JSON.stringify(value))
    assert.notEqual(value.result?.ok, false, JSON.stringify(value))
    return value.result
  }
  const record = (check, details) => report.checks.push({ profile: 'wide', check, passed: true, ...details })
  const field = (form, name) => widget(form).locator(`[data-webui-field-id="${name}"]`)
  const choose = async (form, name, text) => {
    const control = field(form, name)
    const option = control.locator('option').filter({ hasText: text }).first()
    await option.waitFor({ state: 'attached', timeout: 30000 })
    await control.locator('select').selectOption(await option.getAttribute('value'))
  }
  const resumed = process.env.ADAOS_E2E_CREATED_TEST ? JSON.parse(process.env.ADAOS_E2E_CREATED_TEST) : null
  if (resumed) {
    assert.equal(resumed.id, id)
    report.created_test = resumed
    await command('applications').click()
    await widget('project-picker-table').waitFor()
    await widget('project-picker-table').locator('input').first().fill(id)
    await widget('project-picker-table').getByText(resumed.title, { exact: true }).click()
    await widget('design-workbench-header').getByText(resumed.title, { exact: true }).first().waitFor()
  } else {
  await command('applications').click()
  await widget('project-picker-table').getByText('Builder', { exact: true }).waitFor()
  await widget('project-picker-sample').locator('.selector__control').click()
  await page.getByRole('option', { name: 'Тестовые приложения', exact: true }).filter({ visible: true }).last().click()
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll('[data-webui-widget-id="project-picker-table"] tbody tr')]
    return rows.length && rows.every(row => row.textContent.includes('[TEST]'))
  })
  record('picker_filters_test_rows')
  await widget('project-picker-sample').locator('.selector__control').click()
  await page.getByRole('option', { name: 'Нетестовые приложения', exact: true }).filter({ visible: true }).last().click()
  await widget('project-picker-table').getByText('Builder', { exact: true }).waitFor()
  assert.ok(!(await widget('project-picker-table').locator('tbody').innerText()).includes('[TEST]'))
  record('picker_filters_regular_rows')
  await capture('regular-applications')
  await widget('project-picker-create').locator('[data-command-id="create"]').click()
  await widget('new-project-form').waitFor()
  await field('new-project-form', 'title').locator('input').fill(title)
  await field('new-project-form', 'object_id').locator('input').fill(id)
  await capture('creation-ready')
  const createdResponse = responseFor('create_project')
  await widget('new-project-form').getByRole('button', { name: /^создать$/i }).click()
  const created = await success(createdResponse)
  assert.equal(created.object_type, 'project')
  assert.equal(created.primary_object_type, 'scenario')
  assert.equal(created.object_id, id)
  assert.equal(created.title, title)
  report.created_test = { id, title, result: created }
  await fs.writeFile(path.join(process.env.ADAOS_E2E_OUTPUT, 'created-test.json'), JSON.stringify({ created_test: report.created_test }, null, 2) + '\n', 'utf8')
  // The preserved picker may remain under the dismissed creation modal.
  if (await widget('project-picker-table').isVisible()) await closeModal()
  await widget('design-workbench-header').getByText(title, { exact: true }).first().waitFor({ timeout: 30000 })
  record('creation_selects_aggregate_and_primary', { id })
  }
  await command('settings').click()
  await widget('design-codex-settings').waitFor()
  await capture('before-saving-models')
  await choose('chat-side-settings', 'llmModel', 'GPT-5')
  const prototypeSaved = responseFor('set_llm_profile')
  await widget('chat-side-settings').getByRole('button', { name: /^применить$/i }).click()
  await success(prototypeSaved)
  const codexModel = process.env.ADAOS_E2E_CODEX_MODEL || 'gpt-5.5'
  await choose('design-codex-settings', 'model', codexModel.toUpperCase())
  await choose('design-codex-settings', 'reasoning_effort', 'medium')
  const codexSaved = responseFor('set_codex_profile')
  await widget('design-codex-settings').getByRole('button', { name: /^сохранить codex$/i }).click()
  await success(codexSaved)
  await closeModal()
  const reread = responseFor('get_model_settings')
  await command('settings').click()
  const settings = await success(reread)
  assert.equal(settings.execution_ref, `scenario:${id}`)
  assert.equal(settings.prototype_model, 'gpt-5')
  assert.equal(settings.codex_model, codexModel)
  assert.equal(settings.codex_effort, 'medium')
  record('stage_models_persist_at_execution_identity', { settings })
  await capture('saved-models')
  await closeModal()
  const automationBrief = process.env.ADAOS_E2E_AUTOMATION_BRIEF
  if (automationBrief) {
    const followup = process.env.ADAOS_E2E_AUTOMATION_FOLLOWUP === '1'
    assert.ok(resumed, 'Automation may only target a previously created TEST application')
    const state = await widget('design-conversation-side-task').locator('ada-chat-widget')
      .evaluate(el => window.ng.getComponent(el).pageState.getSnapshot())
    assert.equal(state.selectedProjectId, id)
    assert.equal(state.workbench.prototype_revision, '002')
    const intent = { id, brief: automationBrief, followup, requested_model: settings.codex_model,
      requested_effort: settings.codex_effort, created_at: new Date().toISOString() }
    await fs.writeFile(path.join(process.env.ADAOS_E2E_OUTPUT, 'automation-intent.json'), JSON.stringify(intent, null, 2) + '\n', { encoding: 'utf8', flag: 'wx' })
    await command('implement').click()
    const form = followup ? 'automation-followup' : 'automation-start'
    await field(form, followup ? 'text' : 'implementation_brief').locator('textarea').fill(automationBrief)
    await capture('automation-ready')
    const tool = followup ? 'submit_automation' : 'start_automation'
    const pending = page.waitForResponse(response => response.request().postData()?.includes(`builder_sdk_control_skill:${tool}`), { timeout: 180000 })
    void pending.catch(() => {})
    await widget(form).getByRole('button', { name: followup ? /^отправить новую итерацию$/i : /^запустить$/i }).click()
    const response = await pending
    const value = await response.json()
    await fs.writeFile(path.join(process.env.ADAOS_E2E_OUTPUT, 'automation-start.json'), JSON.stringify(value, null, 2) + '\n', 'utf8')
    assert.equal(value.ok, true, JSON.stringify(value))
    assert.notEqual(value.result?.ok, false, JSON.stringify(value))
    const session = value.result?.session
    assert.equal(session?.object_id, id)
    assert.ok(session.current_task_id)
    assert.equal(session.agent_profile?.model, codexModel)
    assert.equal(session.agent_profile?.reasoning_effort, settings.codex_effort)
    if (followup) {
      assert.ok(session.iteration > 0)
      assert.ok((session.agent_profile_history || []).every(previous => previous.task_id !== session.current_task_id))
    }
    record('automation_started_from_native_form', { result: value.result })
    await capture('automation-started')
    return
  }
  const prompt = process.env.ADAOS_E2E_PROTOTYPE_PROMPT
  if (prompt) {
    const chat = widget('design-conversation-side-task')
    const before = await chat.locator('ada-chat-widget').evaluate(el => {
      const instance = window.ng.getComponent(el)
      return { widget: instance.widget, state: instance.pageState.getSnapshot(), messages: instance.messages }
    })
    assert.equal(before.state.selectedProjectId, id)
    assert.equal(before.state.workbench?.object_id, id)
    assert.equal(before.state.workflowActivePhase, 'prototype')
    const baseRevision = process.env.ADAOS_E2E_BASE_REVISION || null
    assert.equal(before.state.workbench.prototype_revision || null, baseRevision,
      'The current revision must match the explicitly requested new or follow-up operation')
    assert.ok(before.state.builderThreadId.includes(id))
    const intent = { id, prompt, thread_id: before.state.builderThreadId, conversation_id: before.state.builderConversationId,
      created_at: new Date().toISOString(), automation_authorized: false, base_revision: baseRevision }
    const marker = await fs.open(path.join(process.env.ADAOS_E2E_OUTPUT, 'prototype-intent.json'), 'wx')
    await marker.writeFile(JSON.stringify(intent, null, 2) + '\n', 'utf8')
    await marker.close()
    await chat.locator('textarea').fill(prompt)
    await chat.locator('textarea').press('Control+Enter')
    console.log(JSON.stringify({ prototype: 'submitted-once', id }))
    await page.waitForFunction(({ id, prompt }) => {
      const element = document.querySelector('[data-webui-widget-id="design-conversation-side-task"] ada-chat-widget')
      const instance = element && window.ng?.getComponent(element)
      return instance?.messages?.some(message => message.text === prompt || message.content === prompt)
    }, { id, prompt }, { timeout: 30000 })
    await capture('prototype-request')
    const deadline = Date.now() + 15 * 60 * 1000
    while (Date.now() < deadline) {
      const current = await chat.locator('ada-chat-widget').evaluate(el => {
        const instance = window.ng.getComponent(el)
        return { state: instance.pageState.getSnapshot(), messages: instance.messages }
      })
      await fs.writeFile(path.join(process.env.ADAOS_E2E_OUTPUT, 'prototype-observation.json'), JSON.stringify(current, null, 2) + '\n', 'utf8')
      const failure = current.messages?.find(message =>
        ['failed', 'interrupted'].includes(message.progress_status) && Number(message.ts) * 1000 >= Date.parse(intent.created_at))
      if (failure) {
        report.prototype_failure = { id: failure.id, job_id: failure.progress_group_id, text: failure.text }
        await capture('prototype-failed')
        throw new Error(`Prototype request failed: ${failure.progress_group_id}; retained diagnostic, no automatic resubmission`)
      }
      if (current.state.workbench?.object_id === id && current.state.workbench?.prototype_revision
        && current.state.workbench.prototype_revision !== baseRevision) {
        record('prototype_generated_via_native_chat', { revision: current.state.workbench.prototype_revision })
        await capture('prototype-result')
        return
      }
      await new Promise(resolve => setTimeout(resolve, 2000))
    }
    throw new Error('Prototype observation deadline reached; inspect existing request, do not automatically resend')
  }
}
