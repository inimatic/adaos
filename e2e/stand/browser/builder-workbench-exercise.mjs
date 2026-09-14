import assert from 'node:assert/strict'

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
  await command('applications').click()
  await widget('project-picker-table').getByText('Builder', { exact: true }).waitFor()
  await widget('project-picker-sample').locator('.selector__control').click()
  await page.locator('.selector__option').getByText('Тестовые приложения', { exact: true }).click()
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll('[data-webui-widget-id="project-picker-table"] tbody tr')]
    return rows.length && rows.every(row => row.textContent.includes('[TEST]'))
  })
  record('picker_filters_test_rows')
  await widget('project-picker-sample').locator('.selector__control').click()
  await page.locator('.selector__option').getByText('Нетестовые приложения', { exact: true }).click()
  await widget('project-picker-table').getByText('Builder', { exact: true }).waitFor()
  assert.ok(!(await widget('project-picker-table').locator('tbody').innerText()).includes('[TEST]'))
  record('picker_filters_regular_rows')
  await capture('regular-applications')
  await widget('project-picker-create').getByRole('button').click()
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
  // The preserved picker may remain under the dismissed creation modal.
  if (await widget('project-picker-table').isVisible()) await closeModal()
  await widget('design-workbench-header').getByText(title, { exact: true }).first().waitFor({ timeout: 30000 })
  record('creation_selects_aggregate_and_primary', { id })
  await command('settings').click()
  await field('chat-side-settings', 'llmModel').locator('option[value="gpt-5"]').waitFor({ state: 'attached' })
  await field('chat-side-settings', 'llmModel').locator('select').selectOption('gpt-5')
  const prototypeSaved = responseFor('set_llm_profile')
  await widget('chat-side-settings').getByRole('button', { name: /^применить$/i }).click()
  await success(prototypeSaved)
  await field('design-codex-settings', 'model').locator('option[value="gpt-5.4"]').waitFor({ state: 'attached' })
  await field('design-codex-settings', 'model').locator('select').selectOption('gpt-5.4')
  await field('design-codex-settings', 'reasoning_effort').locator('select').selectOption('medium')
  const codexSaved = responseFor('set_codex_profile')
  await widget('design-codex-settings').getByRole('button', { name: /^сохранить codex$/i }).click()
  await success(codexSaved)
  await closeModal()
  const reread = responseFor('get_model_settings')
  await command('settings').click()
  const settings = await success(reread)
  assert.equal(settings.execution_ref, `scenario:${id}`)
  assert.equal(settings.prototype_model, 'gpt-5')
  assert.equal(settings.codex_model, 'gpt-5.4')
  assert.equal(settings.codex_effort, 'medium')
  record('stage_models_persist_at_execution_identity', { settings })
  await capture('saved-models')
  await closeModal()
}
