import assert from 'node:assert/strict'

// A successful response/DOM echo is not a persistence oracle. The caller must
// perform an independent provider read and reopen the real editor.
export async function assertContractWriteProof({ response, resourceType, recordId, expected, readRecord, reopen }) {
  const body = response.request().postDataJSON()
  assert.equal(response.ok(), true, 'Confirmed write was rejected')
  const result = await response.json()
  assert.equal(result.ok, true, 'Confirmed write did not succeed')
  assert.equal(body.resource_type, resourceType)
  assert.equal(body.record_id, recordId)
  assert.equal(body.operation_id, 'update')
  assert.equal(body.context?.prototype_command_id, 'complete')
  assert.deepEqual(body.payload, expected)
  const stored = await readRecord()
  assert.equal(stored.id, recordId)
  for (const [key, value] of Object.entries(expected)) assert.deepEqual(stored[key], value, `Unpersisted field: ${key}`)
  const reopened = await reopen()
  for (const [key, value] of Object.entries(expected)) assert.deepEqual(reopened[key], value, `Reopened field differs: ${key}`)
  return { request: body, persisted: stored, reopened }
}
