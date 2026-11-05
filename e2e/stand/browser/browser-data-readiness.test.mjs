import test from 'node:test'
import assert from 'node:assert/strict'
import {missingRequiredData} from './browser-data-readiness.mjs'
test('hidden, absent, static and stale widgets cannot satisfy initial runtime data', () => {
  const required = ['catalog']
  assert.deepEqual(missingRequiredData(required, []), required)
  for (const change of [{kind: 'static'}, {state: 'stale'}, {has_value: 'false'}]) {
    assert.deepEqual(missingRequiredData(required, [{id:'catalog',kind:'mcp',state:'ready',has_value:'true',...change}]), required)
  }
  assert.deepEqual(missingRequiredData(required, [{id:'catalog',kind:'mcp',state:'ready',has_value:'true'}]), [])
  assert.deepEqual(missingRequiredData([], []), [])
})
