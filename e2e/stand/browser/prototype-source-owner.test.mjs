import test from 'node:test'
import assert from 'node:assert/strict'
import { sourceCollectionOwner } from './prototype-source-owner.mjs'

test('A source-pinned main view never retargets to a peer when a mandatory field disappears', () => {
  const views = [
    { id: 'main', role: 'collection', resource_ref: 'jobs', field_refs: ['title'] },
    { id: 'secondary', role: 'collection', resource_ref: 'jobs', field_refs: ['title', 'team_id'] },
  ]
  const input = { resourceRef: 'jobs', fieldRef: 'team_id', ownerRefs: ['view:main'] }
  assert.throws(() => sourceCollectionOwner(views, input), /team_id is absent.*main/)
  views[0].field_refs.push('team_id')
  assert.equal(sourceCollectionOwner(views.toReversed(), input).id, 'main')
  assert.throws(() => sourceCollectionOwner(views, { ...input, ownerRefs: [] }), /exact source-owned/)
  assert.throws(() => sourceCollectionOwner(views, { ...input, resourceRef: 'other' }), /another resource/)
  assert.throws(() => sourceCollectionOwner(views, { ...input, ownerRefs: ['view:missing'] }), /exact source-owned/)
})
