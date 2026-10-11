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

export function typedSelectionParent(semantic, resourceId, filter, value) {
  if (!filter) return null
  const source = semantic.views?.find(view => view.id === filter.source_view_ref && view.role === 'collection')
  const key = filter.source_field_ref || 'id'
  const link = directedRelationships(semantic, resourceId).find(link => link.from_field_ref === filter.field_ref
    && link.to_resource_ref === source?.resource_ref && link.to_field_ref === key)
  const resource = semantic.resources?.find(item => item.id === source?.resource_ref)
  if (!source || !link || !resource || (key !== 'id' && !resource.fields?.some(field => field.id === key))) return null
  const matches = (resource.records || []).filter(record => record[key] === value)
  return matches.length === 1 ? { source, record: matches[0], key } : null
}
