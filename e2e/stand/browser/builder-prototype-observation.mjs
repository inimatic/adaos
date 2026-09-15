import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'

// Attach to an already submitted intent. This function cannot send chat messages.
export async function observePrototype({ chat, intent, output, record, capture }) {
  assert.match(intent.id, /^workbench_test_[a-z0-9_]+$/)
  assert.equal(intent.automation_authorized, false)
  const deadline = Date.now() + 15 * 60 * 1000
  while (Date.now() < deadline) {
    const current = await chat.locator('ada-chat-widget').evaluate(el => {
      const instance = window.ng.getComponent(el)
      return { state: instance.pageState.getSnapshot(), messages: instance.messages }
    })
    assert.equal(current.state.selectedProjectId, intent.id)
    assert.equal(current.state.builderThreadId, intent.thread_id)
    await fs.writeFile(path.join(output, 'prototype-observation.json'), JSON.stringify(current, null, 2) + '\n', 'utf8')
    const failure = current.messages?.find(message =>
      ['failed', 'interrupted'].includes(message.progress_status) && Number(message.ts) * 1000 >= Date.parse(intent.created_at))
    if (failure) {
      await capture('prototype-failed')
      throw new Error(`Prototype request failed: ${failure.progress_group_id}; retained diagnostic, no automatic resubmission`)
    }
    const workbench = current.state.workbench
    if (workbench?.object_id === intent.id && workbench.prototype_revision
        && workbench.prototype_revision !== intent.base_revision) {
      if (intent.new_change) assert.notEqual(workbench.change_id, intent.previous_change_id,
        'A published application must retain the completed Change and start a successor')
      record('prototype_generated_via_native_chat', { revision: workbench.prototype_revision, change_id: workbench.change_id })
      await capture('prototype-result')
      return
    }
    await new Promise(resolve => setTimeout(resolve, 2000))
  }
  throw new Error('Prototype observation deadline reached; inspect the existing request, do not automatically resend')
}
