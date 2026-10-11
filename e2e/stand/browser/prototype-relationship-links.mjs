// Inspect a declared typed join, without inventing an inferred relationship.
export function directedRelationships(semantic, resourceId) {
  return (semantic.relationships || []).flatMap(link => {
    if (link.from_resource_ref === resourceId) return [link]
    if (link.to_resource_ref !== resourceId || !['one_to_many', 'one_to_one'].includes(link.cardinality)) return []
    return [{ ...link, from_resource_ref: link.to_resource_ref, to_resource_ref: link.from_resource_ref,
      from_field_ref: link.to_field_ref, to_field_ref: link.from_field_ref,
      cardinality: link.cardinality === 'one_to_many' ? 'many_to_one' : 'one_to_one' }]
  })
}
