// A one-item request is a legitimate alternative to a request-header/line
// resource pair. Qualification must not force an extra application concept.
import { directedRelationships, typedSelectionParent } from './prototype-relationship-links.mjs'
import { fieldSemanticText } from './prototype-field-semantics.mjs'
export function directRequestCompositionFields(form, action, semantic) {
  const submitted = field => !field.readOnly && action?.params?.payload?.[field.id] === `$event.values.${field.id}`
  const fields = (form?.inputs?.fields || []).filter(submitted)
  const quantity = fields.find(field => ['number', 'integer'].includes(field.type)
    && /quantity|qty|amount|кол[ -]?во|количеств/i.test(fieldSemanticText(field)))
  const offer = fields.find(field => {
    const type = field.optionsDataSource?.resourceType || ''
    const resource = semantic.resources.find(item => type.endsWith(`.${item.id}`))
    if (!resource) return false
    const names = resource.fields.map(fieldSemanticText).join(' ')
    return /price|cost|цена|стоимост/i.test(names) && /delivery|lead|days|срок|дн[еи]/i.test(names)
  })
  if (quantity && offer) return { quantity, offer }
  if (!quantity) return null
  const matches = (field, pattern) => pattern.test(fieldSemanticText(field))
  const price = fields.find(field => ['number', 'integer'].includes(field.type) && matches(field, /price|cost|цена|стоимость/i))
  const lead = fields.find(field => ['number', 'integer'].includes(field.type) && matches(field, /delivery|lead|days|срок|дн[еи]/i))
  const currency = fields.find(field => matches(field, /currency|валют/i))
  const supplier = fields.find(field => ['shortText', 'longText', 'dropdown', 'singleChoice'].includes(field.type)
    && matches(field, /supplier|vendor|поставщик/i))
  const item = fields.find(field => field.optionsDataSource && matches(field, /item|product|товар|позици/i))
  if (!price || !currency || !supplier || !item) return linkedItemComposition(action, semantic, fields, quantity)
  // Manual quote copying is also a valid public workflow. Require a real
  // price/lead/currency resource and a typed item relationship. Suppliers may
  // legitimately be quote text/choice values rather than a separate resource.
  for (const resource of semantic.resources) {
    const links = directedRelationships(semantic, resource.id)
    const supplierLink = links.find(link => supplier.optionsDataSource?.resourceType?.endsWith(`.${link.to_resource_ref}`))
    const itemLink = links.find(link => item.optionsDataSource.resourceType.endsWith(`.${link.to_resource_ref}`))
    const leaf = field => field.id.split('.').at(-1)
    const unique = pattern => {
      const candidates = resource.fields.filter(field => matches(field, pattern))
      return candidates.length === 1 ? candidates[0] : null
    }
    const quotePrice = resource.fields.find(field => leaf(field) === leaf(price)) || unique(/price|cost|цена|стоимость/i)
    const quoteLead = (lead && resource.fields.find(field => leaf(field) === leaf(lead))) || unique(/delivery|lead|days|срок|дн[еи]/i)
    const quoteCurrency = resource.fields.find(field => leaf(field) === leaf(currency)) || unique(/currency|валют/i)
    const quoteSupplier = supplierLink ? null : unique(/supplier|vendor|поставщик/i)
    if (!itemLink || (supplier.optionsDataSource ? !supplierLink : !quoteSupplier)
      || !quotePrice || !quoteLead || !quoteCurrency || !resource.records?.length) continue
    const quoteFields = { price: quotePrice.id, lead: quoteLead.id, currency: quoteCurrency.id,
      supplier: supplierLink?.from_field_ref || quoteSupplier.id, item: itemLink.from_field_ref }
    const view = (semantic.views || []).find(view => view.role === 'collection' && view.resource_ref === resource.id
      && [quoteFields.price, quoteFields.lead, quoteFields.currency, quoteFields.supplier].every(field => view.field_refs.includes(field))
      && view.selection_filter?.field_ref === itemLink.from_field_ref
      && (view.selection_filter.source_field_ref || 'id') === (itemLink.to_field_ref || 'id')
      && semantic.views.some(source => source.id === view.selection_filter.source_view_ref && source.resource_ref === itemLink.to_resource_ref))
    const firstQuote = resource.records[0]
    const selectedParent = view && view.selection_filter.source_field_ref
      ? typedSelectionParent(semantic, resource.id, view.selection_filter, firstQuote[itemLink.from_field_ref]) : null
    const comparisonQuotes = resource.records.filter(record => record[itemLink.from_field_ref] === firstQuote[itemLink.from_field_ref]
      && Number.isFinite(record[quoteFields.price]) && Number.isFinite(record[quoteFields.lead])
      && record[quoteFields.currency] && record[quoteFields.supplier])
    const comparison = view && (!view.selection_filter.source_field_ref || selectedParent)
      && typeof firstQuote[itemLink.from_field_ref] === 'string'
      && new Set(comparisonQuotes.map(record => record[quoteFields.supplier])).size >= 2
      ? { resource, view, parentField: itemLink.from_field_ref, fields: quoteFields,
          quoteIds: comparisonQuotes.map(record => record.id), sourceViewId: view.selection_filter.source_view_ref,
          selectionId: selectedParent?.record.id || firstQuote[itemLink.from_field_ref], filterValue: firstQuote[itemLink.from_field_ref],
          itemId: firstQuote[itemLink.from_field_ref] } : null
    return { quantity, manual: { price, lead, currency, supplier, item, resource, comparison,
      quoteFields } }
  }
  return linkedItemComposition(action, semantic, fields, quantity)
}

// The task asks to compare offers, not necessarily to copy a quote into the
// request. A typed one-item request is valid if the comparison is independently
// reachable and contains real price/lead/currency data from multiple suppliers.
function linkedItemComposition(action, semantic, fields, quantity) {
  const request = semantic.resources.find(resource => action.target?.endsWith(`.${resource.id}`))
  if (!request) return null
  const item = fields.find(field => field.optionsDataSource && (semantic.relationships || []).some(link =>
    link.from_resource_ref === request.id && link.from_field_ref === field.id
    && field.optionsDataSource.resourceType?.endsWith(`.${link.to_resource_ref}`)))
  if (!item) return null
  for (const resource of semantic.resources) {
    const parent = directedRelationships(semantic, resource.id).find(link => link.from_resource_ref === resource.id
      && link.to_resource_ref === request.id && link.to_field_ref === 'id')
    if (!parent) continue
    const match = pattern => resource.fields.find(field => pattern.test(fieldSemanticText(field)))?.id
    const price = match(/price|cost|цена|стоимость/i)
    const lead = match(/delivery|lead|days|срок|дн[еи]/i)
    const supplier = match(/supplier|vendor|поставщик/i)
    const currency = match(/currency|валют/i)
    if (![price, lead, supplier, currency].every(Boolean)) continue
    const view = (semantic.views || []).find(view => view.role === 'collection' && view.resource_ref === resource.id
      && [price, lead, supplier, currency].every(field => view.field_refs.includes(field))
      && view.selection_filter?.source_view_ref && view.selection_filter.field_ref === parent.from_field_ref
      && semantic.views.some(source => source.id === view.selection_filter.source_view_ref && source.resource_ref === request.id))
    if (!view) continue
    for (const requestRecord of request.records || []) {
      const itemId = requestRecord[item.id]
      if (typeof itemId !== 'string' || !itemId) continue
      const quotes = (resource.records || []).filter(record => record[parent.from_field_ref] === requestRecord.id
        && Number.isFinite(record[price]) && Number.isFinite(record[lead])
        && typeof record[supplier] === 'string' && record[supplier].trim()
        && typeof record[currency] === 'string' && record[currency].trim())
      if (quotes.length < 2 || new Set(quotes.map(record => record[supplier])).size < 2) continue
      return { quantity, item, basic: { requestId: requestRecord.id, resource, view,
        itemId, parentField: parent.from_field_ref, fields: { price, lead, supplier, currency }, quoteIds: quotes.map(record => record.id) } }
    }
  }
  return null
}

export function transitionPersistenceExpectations(action, command, priorRecord) {
  const fixed = command?.fixed_values || {}
  if (!Object.keys(fixed).length) throw new Error('Approval transition has no explicit destination values')
  const expected = {}
  for (const [key, value] of Object.entries(action?.params?.payload || {})) {
    if (typeof value === 'string' && value.startsWith('$')) {
      const source = /^\$event\.values\.(.+)$/.exec(value)?.[1]
      if (!source || !command.input_field_refs?.includes(source) || !Object.hasOwn(priorRecord, source)) {
        throw new Error(`Unqualified dynamic transition value: ${key}`)
      }
      expected[key] = priorRecord[source]
    } else expected[key] = value
  }
  for (const [key, value] of Object.entries(fixed)) {
    if (action.params.payload[key] !== value) throw new Error('Compiled approval destination disagrees with semantic transition')
    expected[key] = value
  }
  return expected
}
