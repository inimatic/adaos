import { test } from 'node:test'
import assert from 'node:assert/strict'
import { canonicalRenderedWidgetSources, canonicalWidgetSources } from './widget-source-parity.mjs'

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

test('runtime-derived node transport binding does not change pinned source identity', () => {
  const rendered = original.map(widget => ({
    ...widget,
    dataSource: { ...widget.dataSource, nodeId: 'node-1', scope: 'node' },
  }))
  assert.deepEqual(canonicalRenderedWidgetSources(rendered, original), canonicalWidgetSources(original))
})

test('explicit scope and unrelated rendered source changes remain identity changes', () => {
  const explicitlyPinned = [{
    ...original[0],
    dataSource: { ...original[0].dataSource, nodeId: 'node-1', scope: 'node' },
  }]
  const wrongNode = [{
    ...explicitlyPinned[0],
    dataSource: { ...explicitlyPinned[0].dataSource, nodeId: 'node-2' },
  }]
  assert.notDeepEqual(
    canonicalRenderedWidgetSources(wrongNode, explicitlyPinned),
    canonicalWidgetSources(explicitlyPinned),
  )
  const unexpected = [{
    ...original[0],
    dataSource: { ...original[0].dataSource, params: { mode: 'other' } },
  }]
  assert.notDeepEqual(
    canonicalRenderedWidgetSources(unexpected, [original[0]]),
    canonicalWidgetSources([original[0]]),
  )
})
