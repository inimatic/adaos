import test from 'node:test'
import assert from 'node:assert/strict'
import { assessUsability } from './prototype-usability.mjs'

const baseline = { viewportWidth: 390, documentWidth: 390, actions: [{ name: 'Сохранить', width: 96, height: 40 }] }
test('Named reachable actions pass minimum usability floors', () => {
  assert.equal(assessUsability(baseline, { compact: true }).ok, true)
})
test('Page overflow is not mistaken for a functional pass', () => {
  const result = assessUsability({ ...baseline, documentWidth: 700 })
  assert.equal(result.gates.no_page_horizontal_overflow, false)
  assert.equal(result.ok, false)
})
test('Unnamed visible actions fail independently of target size', () => {
  const result = assessUsability({ ...baseline, actions: [{ name: ' ', width: 96, height: 40 }] }, { compact: true })
  assert.equal(result.gates.visible_actions_have_names, false)
  assert.equal(result.metrics.unnamed_actions, 1)
})
test('Compact undersized actions are reported without inventing a human UX score', () => {
  const result = assessUsability({ ...baseline, actions: [{ name: 'Edit', width: 18, height: 20 }] }, { compact: true })
  assert.equal(result.gates.compact_action_minimum_footprint, false)
  assert.equal(result.metrics.undersized_actions, 1)
})
test('No visible actions is not inferred to mean task completion or discoverability', () => {
  const result = assessUsability({ ...baseline, actions: [] })
  assert.equal(result.metrics.visible_actions, 0)
  assert.match(result.scope, /Current visible surface only/)
})
