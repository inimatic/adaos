import { test } from 'node:test'
import assert from 'node:assert/strict'
import { canonicalWidgetSources } from './widget-source-parity.mjs'

const original = [
  { id: 'details', type: 'item.details', area: 'supporting', dataSource: { kind: 'skill', name: 'sample.get' } },
  { id: 'list', type: 'ui.table', area: 'primary', dataSource: { kind: 'skill', name: 'sample.list' } },
]

test('layout grouping may change DOM order without changing widget sources', () => {
  assert.deepEqual(canonicalWidgetSources([...original].reverse()), canonicalWidgetSources(original))
})

test('different sources, types, areas and missing or duplicate widgets still fail', () => {
  for (const changed of [
    [{ ...original[0], dataSource: { kind: 'skill', name: 'sample.other' } }, original[1]],
    [{ ...original[0], type: 'ui.list' }, original[1]],
    [{ ...original[0], area: 'primary' }, original[1]],
    [original[0]],
    [original[0], original[0]],
  ]) assert.notDeepEqual(canonicalWidgetSources(changed), canonicalWidgetSources(original))
})
