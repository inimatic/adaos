import test from 'node:test'
import assert from 'node:assert/strict'
import {isExpectedFixtureRestartReadError as expected} from './controlled-faults.mjs'

test('a refused or reset owned read is expected only during the declared restart', () => {
  const base = {restartWindow: true, method: 'POST', tool: 'natural_retouch_skill:query_records'}
  for (const message of ['route.fetch: connect ECONNREFUSED', 'route.fetch: read ECONNRESET']) {
    assert.equal(expected({...base, message}), true)
    assert.equal(expected({...base, message, restartWindow: false}), false)
    assert.equal(expected({...base, message, tool: 'natural_retouch_skill:run_command'}), false)
    assert.equal(expected({...base, message, tool: 'natural_retouch_skill:save_mask'}), false)
    assert.equal(expected({...base, message, tool: 'other_skill:query_records'}), false)
  }
  assert.equal(expected({...base, message: 'route.fetch: Timeout'}), false)
})
