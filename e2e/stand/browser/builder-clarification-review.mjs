import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { expect } from '@playwright/test'

export async function reviewClarification({ page, widget, capture, report, output, intent }) {
  const state = () => widget('design-current-work').locator('ada-details-widget')
    .evaluate(el => window.ng.getComponent(el).state.getSnapshot().workbench)
  const responseFor = tool => page.waitForResponse(response =>
    response.request().postData()?.includes(`builder_sdk_control_skill:${tool}`), { timeout: 180000 })
  const successful = async pending => {
    const value = await (await pending).json()
    assert.equal(value.ok, true, JSON.stringify(value))
    assert.notEqual(value.result?.ok, false, JSON.stringify(value))
    return value
  }
  const before = await state()
  assert.equal(before.object_id, intent.application_id)
  assert.equal(before.change_id, intent.change_id)
  assert.equal(before.automation_task_id, intent.source_run_id)
  assert.equal(before.clarification?.pending, true)
  assert.equal(before.clarification.can_resume, Boolean(intent.previous_review))
  assert.equal(before.clarification.binding.run_id, intent.source_run_id)
  assert.equal(before.clarification.questions.length, intent.answers.length)
  for (const expected of intent.answers) {
    const actual = before.clarification.questions.find(row => row.id === expected.question_id)
    assert.equal(actual?.question, expected.question)
    assert.equal(actual?.answer, intent.previous_review ? expected.answer : '')
    assert.ok(expected.answer.trim())
  }
  await fs.writeFile(path.join(output, 'clarification-admission.json'), JSON.stringify({
    ...intent, binding: before.clarification.binding,
    interaction_id: before.clarification.interaction_id,
    reviewer: { id: 'agent:codex-independent-test-review', delegated_by: 'user:local' },
  }, null, 2) + '\n', { encoding: 'utf8', flag: 'wx' })
  const actions = widget('model-clarification-actions')
  if (!intent.previous_review) await expect(actions.locator('[data-command-id="resume"]')).toBeDisabled()
  await actions.locator('[data-command-id="questions"]').click()
  await capture('clarification-pending')
  for (const expected of intent.previous_review ? [] : intent.answers) {
    await widget('model-clarification-questions').getByText(expected.question, { exact: true }).click()
    await widget('model-question-form').locator('textarea').fill(expected.answer)
    const pending = responseFor('answer_clarification')
    void pending.catch(() => {})
    await widget('model-question-form').getByRole('button', { name: /^(save answer|сохранить ответ)$/i }).click()
    const value = await successful(pending)
    assert.equal(value.result.clarification.interaction_id, before.clarification.interaction_id)
    assert.equal(value.result.clarification.binding.run_id, intent.source_run_id)
    assert.equal((await state()).automation_task_id, intent.source_run_id, 'Saving must not start Codex')
  }
  await expect.poll(async () => (await state()).clarification.can_resume, { timeout: 60000 }).toBe(true)
  await capture('clarification-answered')
  const refreshed = responseFor('get_workbench')
  void refreshed.catch(() => {})
  await page.reload({ waitUntil: 'domcontentloaded' })
  await successful(refreshed)
  await expect.poll(async () => (await state()).clarification?.can_resume, { timeout: 60000 }).toBe(true)
  const reopened = await state()
  assert.equal(reopened.automation_task_id, intent.source_run_id)
  assert.equal(reopened.clarification.interaction_id, before.clarification.interaction_id)
  for (const expected of intent.answers) {
    assert.equal(reopened.clarification.questions.find(row => row.id === expected.question_id)?.answer, expected.answer)
  }
  report.checks.push({ check: 'native_clarification_answers_survive_reload_without_resume', passed: true,
    interaction_id: reopened.clarification.interaction_id, source_run_id: intent.source_run_id })
  await capture('clarification-reloaded')
  const resumed = responseFor('resume_clarification')
  void resumed.catch(() => {})
  await actions.locator('[data-command-id="resume"]').click()
  let response = await (await resumed).json()
  if (response.detail?.error === 'action_approval_required') {
    const actionId = response.detail.pending_action_id
    assert.equal(response.detail.tool, 'builder_sdk_control_skill:resume_clarification')
    assert.ok(actionId)
    await fs.writeFile(path.join(output, 'clarification-network-approval.json'), JSON.stringify(response, null, 2) + '\n', 'utf8')
    if (!await page.locator('.pending-action-item').count()) await page.locator('.pending-actions-fab').click()
    await page.waitForFunction(actionId => [...document.querySelectorAll('.pending-action-item')]
      .some(el => window.ng?.getContext(el)?.$implicit?.id === actionId), actionId, { timeout: 30000 })
    const index = await page.locator('.pending-action-item').evaluateAll((items, actionId) =>
      items.findIndex(el => window.ng?.getContext(el)?.$implicit?.id === actionId), actionId)
    assert.ok(index >= 0, 'Exact owned continuation approval must be visible')
    await capture('clarification-network-review')
    const approved = responseFor('resume_clarification')
    void approved.catch(() => {})
    await page.locator('.pending-action-item').nth(index).getByRole('button', { name: /^(подтвердить|approve)(:|$)/i }).click()
    response = await (await approved).json()
  }
  assert.equal(response.ok, true, JSON.stringify(response))
  assert.notEqual(response.result?.ok, false, JSON.stringify(response))
  const value = response
  await fs.writeFile(path.join(output, 'automation-start.json'), JSON.stringify(value, null, 2) + '\n', 'utf8')
  const session = value.result.session
  assert.equal(session.object_id, intent.application_id)
  assert.equal(session.canonical_change_id, intent.change_id)
  assert.ok(session.current_task_id)
  assert.notEqual(session.current_task_id, intent.source_run_id)
  assert.equal(session.clarification_continuation?.interaction_id, before.clarification.interaction_id)
  report.checks.push({ check: 'native_explicit_same_change_clarification_continuation', passed: true,
    source_run_id: intent.source_run_id, continuation_run_id: session.current_task_id,
    response_id: reopened.clarification.response_id, change_id: intent.change_id })
  await capture('clarification-continued')
}
