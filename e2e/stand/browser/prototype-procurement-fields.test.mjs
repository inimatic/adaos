import assert from 'node:assert/strict'
import test from 'node:test'
import { directRequestCompositionFields, transitionPersistenceExpectations } from './prototype-procurement-fields.mjs'

const form = { inputs: { fields: [{ id: 'quantity', type: 'number' },
  { id: 'offer_id', type: 'dropdown', optionsDataSource: { resourceType: 'prototype.project.test_one.offers' } }] } }
const action = { params: { payload: { quantity: '$event.values.quantity', offer_id: '$event.values.offer_id' } } }
const semantic = { resources: [{ id: 'offers', fields: [{ id: 'unit_price' }, { id: 'delivery_days' }] }] }

test('a one-item request can prove offer and quantity without a separate line resource', () => {
  assert.deepEqual(directRequestCompositionFields(form, action, semantic), {
    quantity: form.inputs.fields[0], offer: form.inputs.fields[1],
  })
})

test('an unsubmitted quantity or arbitrary lookup cannot stand in for request composition', () => {
  assert.equal(directRequestCompositionFields(form, { params: { payload: { offer_id: '$event.values.offer_id' } } }, semantic), null)
  assert.equal(directRequestCompositionFields(form, action, { resources: [{ id: 'offers', fields: [{ id: 'name' }] }] }), null)
})

test('manual composition must copy a real typed supplier quote, including price and currency', () => {
  const fields = [{ id: 'requests.qty', type: 'number' }, { id: 'requests.price', type: 'number' },
    { id: 'requests.lead_days', type: 'number' }, { id: 'requests.currency', type: 'singleChoice' },
    { id: 'requests.item_id', type: 'dropdown', optionsDataSource: { resourceType: 'prototype.project.test_one.items' } },
    { id: 'requests.supplier_id', type: 'dropdown', optionsDataSource: { resourceType: 'prototype.project.test_one.suppliers' } }]
  const manualForm = { inputs: { fields } }
  const manualAction = { params: { payload: Object.fromEntries(fields.map(field => [field.id, `$event.values.${field.id}`])) } }
  const guide = { resources: [{ id: 'quotes', fields: [{ id: 'quotes.price' }, { id: 'quotes.lead_days' }, { id: 'quotes.currency' }],
    records: [{ id: 'quote-one' }] }], relationships: [
      { from_resource_ref: 'quotes', to_resource_ref: 'items', from_field_ref: 'quotes.item_id' },
      { from_resource_ref: 'quotes', to_resource_ref: 'suppliers', from_field_ref: 'quotes.supplier_id' },
    ] }
  const result = directRequestCompositionFields(manualForm, manualAction, guide)
  assert.equal(result.manual.resource.id, 'quotes')
  assert.equal(result.manual.quoteFields.supplier, 'quotes.supplier_id')
  assert.equal(result.manual.price.id, 'requests.price')
  assert.equal(directRequestCompositionFields(manualForm, manualAction, { ...guide, relationships: guide.relationships.slice(0, 1) }), null)
  assert.equal(directRequestCompositionFields(manualForm, manualAction, { ...guide, resources: [{ ...guide.resources[0], records: [] }] }), null)
  const withoutLead = { inputs: { fields: fields.filter(field => field.id !== 'requests.lead_days') } }
  const withoutLeadAction = { params: { payload: Object.fromEntries(withoutLead.inputs.fields.map(field => [field.id, `$event.values.${field.id}`])) } }
  const optionalLead = directRequestCompositionFields(withoutLead, withoutLeadAction, guide)
  assert.equal(optionalLead.manual.lead, undefined)
  assert.equal(optionalLead.manual.quoteFields.lead, 'quotes.lead_days')
  assert.equal(directRequestCompositionFields(withoutLead, withoutLeadAction, { ...guide,
    resources: [{ ...guide.resources[0], fields: guide.resources[0].fields.filter(field => field.id !== 'quotes.lead_days') }] }), null)
})

test('approval may preserve declared input fields while explicitly fixing the destination state', () => {
  const transition = { fixed_values: { status: 'pending' }, input_field_refs: ['quantity', 'offer_id'] }
  const action = { params: { payload: { status: 'pending', quantity: '$event.values.quantity', offer_id: '$event.values.offer_id' } } }
  assert.deepEqual(transitionPersistenceExpectations(action, transition, { quantity: 7, offer_id: 'offer-one' }),
    { status: 'pending', quantity: 7, offer_id: 'offer-one' })
  assert.throws(() => transitionPersistenceExpectations({ params: { payload: { ...action.params.payload, status: 'draft' } } }, transition,
    { quantity: 7, offer_id: 'offer-one' }), /destination disagrees/)
  assert.throws(() => transitionPersistenceExpectations(action, transition, { quantity: 7 }), /Unqualified dynamic/)
  assert.throws(() => transitionPersistenceExpectations({ params: { payload: { status: 'pending', quantity: '$state.quantity' } } }, transition,
    { quantity: 7 }), /Unqualified dynamic/)
})

function independentComparisonFixture() {
  const fields = [{ id: 'quantity', type: 'number' },
    { id: 'item_id', type: 'dropdown', optionsDataSource: { resourceType: 'prototype.project.test_one.items' } }]
  return { form: { inputs: { fields } },
    action: { target: 'prototype.project.test_one.requests', params: { payload: { quantity: '$event.values.quantity', item_id: '$event.values.item_id' } } },
    semantic: { resources: [{ id: 'requests', fields: [{ id: 'quantity' }, { id: 'item_id' }], records: [{ id: 'request-one', item_id: 'item-one' }] },
      { id: 'offers', fields: ['request_ref', 'supplier', 'unit_price', 'lead_days', 'currency'].map(id => ({ id })),
        records: [{ id: 'quote-a', request_ref: 'request-one', supplier: 'A', unit_price: 20, lead_days: 3, currency: 'USD' },
          { id: 'quote-b', request_ref: 'request-one', supplier: 'B', unit_price: 18, lead_days: 5, currency: 'USD' }] }],
      relationships: [{ from_resource_ref: 'requests', from_field_ref: 'item_id', to_resource_ref: 'items', to_field_ref: 'id' },
        { from_resource_ref: 'offers', from_field_ref: 'request_ref', to_resource_ref: 'requests', to_field_ref: 'id' }],
      views: [{ id: 'request-list', role: 'collection', resource_ref: 'requests', field_refs: ['quantity'] },
        { id: 'offer-list', role: 'collection', resource_ref: 'offers', field_refs: ['supplier', 'unit_price', 'lead_days', 'currency'],
          selection_filter: { source_view_ref: 'request-list', field_ref: 'request_ref' } }] } }
}

test('one-item requests need not duplicate independently comparable supplier quotes', () => {
  const { form, action, semantic } = independentComparisonFixture()
  const result = directRequestCompositionFields(form, action, semantic)
  assert.equal(result.quantity.id, 'quantity')
  assert.equal(result.item.id, 'item_id')
  assert.deepEqual(result.basic.quoteIds, ['quote-a', 'quote-b'])
  assert.equal(result.basic.requestId, 'request-one')
  assert.equal(result.basic.itemId, 'item-one')
})

for (const intervention of ['one-supplier', 'different-parents', 'untyped-item', 'hidden-lead', 'invalid-price', 'unsubmitted-item', 'missing-request-item']) {
  test(`independent comparison rejects ${intervention}`, () => {
    const { form, action, semantic } = independentComparisonFixture()
    if (intervention === 'one-supplier') semantic.resources[1].records[1].supplier = 'A'
    if (intervention === 'different-parents') semantic.resources[1].records[1].request_ref = 'other-request'
    if (intervention === 'untyped-item') semantic.relationships.shift()
    if (intervention === 'hidden-lead') semantic.views[1].field_refs.splice(2, 1)
    if (intervention === 'invalid-price') semantic.resources[1].records[1].unit_price = '18'
    if (intervention === 'unsubmitted-item') delete action.params.payload.item_id
    if (intervention === 'missing-request-item') delete semantic.resources[0].records[0].item_id
    assert.equal(directRequestCompositionFields(form, action, semantic), null)
  })
}

test('A typed request-to-offers one_to_many qualifies the same one-item workflow', () => {
  const { form, action, semantic } = independentComparisonFixture()
  const expected = directRequestCompositionFields(form, action, semantic)
  semantic.relationships = semantic.relationships.map(link => link.from_resource_ref === 'offers'
    ? { ...link, from_resource_ref: link.to_resource_ref, to_resource_ref: link.from_resource_ref,
        from_field_ref: link.to_field_ref, to_field_ref: link.from_field_ref, cardinality: 'one_to_many' }
    : link)
  assert.deepEqual(directRequestCompositionFields(form, action, semantic), expected)
})

test('an additional quote editor does not invalidate a typed one-item request and comparison', () => {
  const { form, action, semantic } = independentComparisonFixture()
  semantic.views.push({ id: 'quote-editor', resource_ref: 'offers', role: 'editor', field_refs: ['request_ref', 'supplier', 'unit_price'] })
  semantic.commands = [{ id: 'add-quote', kind: 'create', view_ref: 'quote-editor' }]
  assert.equal(directRequestCompositionFields(form, action, semantic).basic.requestId, 'request-one')
})

function manualComparisonFixture() {
  const fields = [{ id: 'request_qty', type: 'number' }, { id: 'request_unit_price', type: 'number' },
    { id: 'request_currency', type: 'singleChoice' }, { id: 'request_supplier', type: 'shortText' },
    { id: 'request_item_ref', type: 'dropdown', optionsDataSource: { resourceType: 'prototype.project.test_one.items' } }]
  const form = { inputs: { fields } }
  const action = { target: 'prototype.project.test_one.requests', params: {
    payload: Object.fromEntries(fields.map(field => [field.id, `$event.values.${field.id}`])) } }
  const resource = { id: 'offers', fields: ['offer_item_ref', 'offer_supplier', 'offer_unit_price', 'offer_currency', 'offer_lead_days'].map(id => ({ id })),
    records: [{ id: 'quote-one', offer_item_ref: 'item-one', offer_supplier: 'Supplier A', offer_unit_price: 19.5, offer_currency: 'USD', offer_lead_days: 4 },
      { id: 'quote-two', offer_item_ref: 'item-one', offer_supplier: 'Supplier B', offer_unit_price: 18, offer_currency: 'USD', offer_lead_days: 5 }] }
  const semantic = { resources: [resource], relationships: [{ from_resource_ref: 'offers', to_resource_ref: 'items', from_field_ref: 'offer_item_ref' }],
    views: [{ id: 'item-list', resource_ref: 'items', role: 'collection' },
      { id: 'offer-list', resource_ref: 'offers', role: 'collection', field_refs: resource.fields.map(field => field.id),
        selection_filter: { field_ref: 'offer_item_ref', source_view_ref: 'item-list' } }] }
  return { form, action, semantic }
}

test('manual quote copying proves public comparison scoped to the same typed item', () => {
  const { form, action, semantic } = manualComparisonFixture()
  const comparison = directRequestCompositionFields(form, action, semantic).manual.comparison
  assert.deepEqual(comparison.quoteIds, ['quote-one', 'quote-two'])
  assert.equal(comparison.sourceViewId, 'item-list')
  assert.equal(comparison.selectionId, 'item-one')
  assert.equal(comparison.filterValue, 'item-one')
})

for (const intervention of ['one-supplier', 'different-items', 'hidden-lead', 'invalid-price', 'wrong-source', 'indirect-source-field']) {
  test(`manual copying cannot qualify public comparison with ${intervention}`, () => {
    const { form, action, semantic } = manualComparisonFixture()
    if (intervention === 'one-supplier') semantic.resources[0].records[1].offer_supplier = 'Supplier A'
    if (intervention === 'different-items') semantic.resources[0].records[1].offer_item_ref = 'item-other'
    if (intervention === 'hidden-lead') semantic.views[1].field_refs = semantic.views[1].field_refs.filter(field => field !== 'offer_lead_days')
    if (intervention === 'invalid-price') semantic.resources[0].records[1].offer_unit_price = '18'
    if (intervention === 'wrong-source') semantic.views[0].resource_ref = 'requests'
    if (intervention === 'indirect-source-field') semantic.views[1].selection_filter.source_field_ref = 'unverified-other-field'
    assert.equal(directRequestCompositionFields(form, action, semantic).manual.comparison, null)
  })
}

test('Manual quote copying supports a declared unique business-key item relationship', () => {
  const { form, action, semantic } = manualComparisonFixture()
  semantic.resources.push({ id: 'items', fields: [{ id: 'sku' }], records: [{ id: 'item-one', sku: 'SKU-001' }] })
  semantic.relationships[0].to_field_ref = 'sku'
  semantic.views[1].selection_filter.source_field_ref = 'sku'
  semantic.resources[0].records.forEach(record => { record.offer_item_ref = 'SKU-001' })
  const result = directRequestCompositionFields(form, action, semantic).manual.comparison
  assert.equal(result.filterValue, 'SKU-001')
  assert.equal(result.selectionId, 'item-one')
  semantic.resources[1].records.push({ id: 'item-two', sku: 'SKU-001' })
  assert.equal(directRequestCompositionFields(form, action, semantic).manual.comparison, null)
})

test('manual supplier text and resource-prefixed quote fields retain typed item and durable quote provenance', () => {
  const fields = [{ id: 'request_qty', type: 'number' }, { id: 'request_unit_price', type: 'number' },
    { id: 'request_currency', type: 'singleChoice' }, { id: 'request_supplier', type: 'shortText' },
    { id: 'request_item_ref', type: 'dropdown', optionsDataSource: { resourceType: 'prototype.project.test_one.items' } }]
  const form = { inputs: { fields } }
  const action = { target: 'prototype.project.test_one.requests', params: {
    payload: Object.fromEntries(fields.map(field => [field.id, `$event.values.${field.id}`])) } }
  const resource = { id: 'offers', fields: ['offer_item_ref', 'offer_supplier', 'offer_unit_price', 'offer_currency', 'offer_lead_days'].map(id => ({ id })),
    records: [{ id: 'quote-one', offer_item_ref: 'item-one', offer_supplier: 'Supplier A', offer_unit_price: 19.5, offer_currency: 'USD', offer_lead_days: 4 }] }
  const semantic = { resources: [resource], relationships: [{ from_resource_ref: 'offers', to_resource_ref: 'items', from_field_ref: 'offer_item_ref' }] }
  const result = directRequestCompositionFields(form, action, semantic)
  assert.equal(result.manual.supplier.type, 'shortText')
  assert.deepEqual(result.manual.quoteFields, { price: 'offer_unit_price', lead: 'offer_lead_days',
    currency: 'offer_currency', supplier: 'offer_supplier', item: 'offer_item_ref' })
  assert.equal(directRequestCompositionFields(form, action, { ...semantic, relationships: [] }), null)
  assert.equal(directRequestCompositionFields(form, action, { ...semantic, resources: [{ ...resource,
    fields: resource.fields.filter(field => field.id !== 'offer_lead_days') }] }), null)
  const ambiguous = { ...semantic, resources: [{ ...resource, fields: [...resource.fields, { id: 'second_price' }] }] }
  assert.equal(directRequestCompositionFields(form, action, ambiguous), null)
})
