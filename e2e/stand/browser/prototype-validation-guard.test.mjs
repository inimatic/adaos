import test from 'node:test'
import assert from 'node:assert/strict'
import { assertInvalidSubmissionGuard } from './prototype-validation-guard.mjs'

function fixture(disabled) {
  const state = { mutations: 0, clicks: 0, errors: 0, settles: 0 }
  return { state, options: {
    button: { isDisabled: async () => disabled, click: async () => { state.clicks++ } },
    error: { waitFor: async () => { state.errors++ } },
    settle: async () => { state.settles++ }, mutationCount: () => state.mutations,
  } }
}
test('A disabled native submit is a valid guard without clicking or requiring a submit error', async () => {
  const { state, options } = fixture(true)
  assert.equal(await assertInvalidSubmissionGuard(options), 'disabled-submit')
  assert.equal(state.clicks, 0)
  assert.equal(state.errors, 0)
  assert.equal(state.settles, 2)
})
test('An enabled submit must visibly reject the invalid draft', async () => {
  const { state, options } = fixture(false)
  assert.equal(await assertInvalidSubmissionGuard(options), 'validation-error')
  assert.equal(state.clicks, 1)
  assert.equal(state.errors, 1)
})
test('No visible validation error cannot count as rejection', async () => {
  const { options } = fixture(false)
  options.error.waitFor = async () => { throw new Error('No visible error') }
  await assert.rejects(assertInvalidSubmissionGuard(options), /No visible error/)
})
for (const disabled of [false, true]) test(`Unexpected mutation fails even with disabled=${disabled}`, async () => {
  const { state, options } = fixture(disabled)
  options.settle = async () => { state.mutations++ }
  await assert.rejects(assertInvalidSubmissionGuard(options), /resource mutation/)
})
