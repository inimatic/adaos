import { test } from 'node:test'
import assert from 'node:assert/strict'
import { assertContractWriteProof } from './component-contract-write-proof.mjs'

function proof() {
  const expected = { review_note: 'Approved', state: 'done' }
  const request = { resource_type: 'prototype.test.records', record_id: 'record-1', operation_id: 'update',
    context: { prototype_command_id: 'complete' }, payload: expected }
  return { resourceType: request.resource_type, recordId: request.record_id, expected,
    response: { ok: () => true, json: async () => ({ ok: true }), request: () => ({ postDataJSON: () => request }) },
    readRecord: async () => ({ id: 'record-1', ...expected }), reopen: async () => ({ ...expected }) }
}
test('requires accepted write, independent persistence and reopened values', async () => {
  assert.equal((await assertContractWriteProof(proof())).persisted.state, 'done')
})
for (const mutant of ['http_reject', 'logical_reject', 'echo_without_storage', 'stale_editor', 'wrong_command']) {
  test(`rejects ${mutant}`, async () => {
    const input = proof()
    if (mutant === 'http_reject') input.response.ok = () => false
    if (mutant === 'logical_reject') input.response.json = async () => ({ ok: false })
    if (mutant === 'echo_without_storage') input.readRecord = async () => ({ id: 'record-1', state: 'open', review_note: '' })
    if (mutant === 'stale_editor') input.reopen = async () => ({ state: 'open', review_note: '' })
    if (mutant === 'wrong_command') input.response.request().postDataJSON().context.prototype_command_id = 'save'
    await assert.rejects(assertContractWriteProof(input))
  })
}
