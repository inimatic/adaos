import assert from 'node:assert/strict'

// Owner refs come from the admitted source, not from the generated candidate's
// broad evidence claims. A missing field never authorizes choosing another view.
export function sourceCollectionOwner(views, { resourceRef, fieldRef, ownerRefs = [] }) {
  const candidates = ownerRefs.length
    ? views.filter(view => ownerRefs.includes('view:' + view.id))
    : views.filter(view => view.role === 'collection' && view.resource_ref === resourceRef && view.field_refs.includes(fieldRef))
  assert.equal(candidates.length, 1, 'One exact source-owned collection is required; no first/field-bearing peer fallback')
  const owner = candidates[0]
  assert.equal(owner.role, 'collection', 'The source-owned collection cannot become another role')
  assert.equal(owner.resource_ref, resourceRef, 'The source-owned collection cannot move to another resource')
  assert(owner.field_refs.includes(fieldRef), `Required source field ${fieldRef} is absent from source-owned view ${owner.id}`)
  return owner
}
