import test from 'node:test'
import assert from 'node:assert/strict'
import {isExpectedFixtureRestartReadError as expected, isOptionalLocalDiscoveryRequest as discovery} from './controlled-faults.mjs'

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

test('only bounded read-only loopback discovery may use an alternate authority', () => {
  for (const host of ['localhost', '127.0.0.1']) for (const port of [8777, 8778]) {
    for (const path of ['/api/ping', '/api/node/status?profile=probe']) {
      const url = `http://${host}:${port}${path}`
      assert.equal(discovery({url, method: 'GET'}), true)
      for (const method of ['POST', 'PUT', 'DELETE']) assert.equal(discovery({url, method}), false)
    }
  }
  for (const url of ['http://127.0.0.1:9000/api/ping', 'https://localhost:8777/api/ping',
    'http://remote.example:8777/api/ping', 'http://localhost.evil:8777/api/ping',
    'http://localhost:8777/api/tools/call', 'http://localhost:8778/api/photos',
    'http://localhost:8777/api/node/status', 'http://localhost:8777/api/node/status?profile=full',
    'http://localhost:8777/api/node/status?profile=probe&extra=1',
    'http://localhost:8777/api/ping?token=secret', 'http://user@localhost:8777/api/ping', 'invalid']) {
    assert.equal(discovery({url, method: 'GET'}), false, url)
  }
})
