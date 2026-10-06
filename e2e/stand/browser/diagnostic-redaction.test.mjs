import test from 'node:test'
import assert from 'node:assert/strict'
import { redactDiagnosticValue } from './diagnostic-redaction.mjs'

test('browser receipts redact nested credentials without hiding token metrics', () => {
  const source = { request_failures: [{ url: 'https://user:pass@host/x?token=private&webspace=desktop',
    headers: { Authorization: 'Bearer private', 'X-Other': 'Bearer private' } }],
    cached_input_tokens: 2048, access_token: 'private' }
  const redacted = redactDiagnosticValue(source)
  assert.equal(JSON.stringify(redacted).includes('private'), false)
  assert.equal(redacted.cached_input_tokens, 2048)
  assert.match(redacted.request_failures[0].url, /webspace=desktop/)
  assert.equal(source.access_token, 'private')
})
