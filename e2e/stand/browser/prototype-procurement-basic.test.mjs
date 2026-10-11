import assert from 'node:assert/strict'
import test from 'node:test'
import { chromium } from '@playwright/test'
import { quoteFieldCell, supplierComparisonMetadata, visibleQuoteNumber } from './prototype-procurement-basic.mjs'

test('quote numbers accept display decimals and trailing zeros, not empty or unrelated text', () => {
  assert.equal(visibleQuoteNumber(' 485.50 '), 485.5)
  assert.equal(visibleQuoteNumber('1460,00'), 1460)
  assert.equal(visibleQuoteNumber('3'), 3)
  for (const text of ['', 'Supplier 3', '3 days 10 minimum', 'NaN', 'Infinity']) assert.equal(visibleQuoteNumber(text), null)
})

function comparisonFixture() {
  return { resources: [{ id: 'requests', records: [{ id: 'request-one' }], fields: [] },
    { id: 'lines', records: [{ id: 'line-one', request_ref: 'request-one' }], fields: [{ id: 'request_ref' }] },
    { id: 'quotes', fields: ['supplier', 'price', 'lead_days', 'currency', 'delivery_date', 'line_ref'].map(id => ({ id })),
      records: [{ id: 'quote-a', line_ref: 'line-one', supplier: 'A', price: 20, lead_days: 3, currency: 'USD', delivery_date: '2026-10-14' },
        { id: 'quote-b', line_ref: 'line-one', supplier: 'B', price: 18, lead_days: 5, currency: 'USD', delivery_date: '2026-10-16' }] }],
    views: [{ id: 'requests-list', role: 'collection', resource_ref: 'requests', field_refs: [] },
      { id: 'lines-list', role: 'collection', resource_ref: 'lines', field_refs: ['request_ref'],
        selection_filter: { field_ref: 'request_ref', source_view_ref: 'requests-list' } },
      { id: 'quotes-list', role: 'collection', resource_ref: 'quotes', field_refs: ['supplier', 'price', 'lead_days', 'currency'],
        selection_filter: { field_ref: 'line_ref', source_view_ref: 'lines-list' } }],
    relationships: [{ from_resource_ref: 'quotes', from_field_ref: 'line_ref', to_resource_ref: 'lines', to_field_ref: 'id' },
      { from_resource_ref: 'lines', from_field_ref: 'request_ref', to_resource_ref: 'requests', to_field_ref: 'id' }] }
}

test('all request representations compare real supplier quotes through the complete typed public selection chain', () => {
  const metadata = supplierComparisonMetadata(comparisonFixture())
  assert.deepEqual(metadata.selections, [{ viewId: 'requests-list', recordId: 'request-one' }, { viewId: 'lines-list', recordId: 'line-one' }])
  assert.deepEqual(metadata.quoteIds, ['quote-a', 'quote-b'])
  assert.equal(metadata.fields.lead, 'lead_days')
  assert.equal(metadata.filterValue, 'line-one')
})

for (const intervention of ['one-supplier', 'different-lines', 'hidden-price', 'untyped-parent', 'missing-parent', 'indirect-source', 'invalid-price']) {
  test(`uniform supplier comparison rejects ${intervention}`, () => {
    const semantic = comparisonFixture()
    if (intervention === 'one-supplier') semantic.resources[2].records[1].supplier = 'A'
    if (intervention === 'different-lines') semantic.resources[2].records[1].line_ref = 'line-other'
    if (intervention === 'hidden-price') semantic.views[2].field_refs.splice(1, 1)
    if (intervention === 'untyped-parent') semantic.relationships.pop()
    if (intervention === 'missing-parent') semantic.resources[0].records = []
    if (intervention === 'indirect-source') semantic.views[1].selection_filter.source_field_ref = 'unverified-other-id'
    if (intervention === 'invalid-price') semantic.resources[2].records[1].price = '18'
    assert.equal(supplierComparisonMetadata(semantic), null)
  })
}

test('quote checks address exact visible price and lead cells, not concatenated adjacent numbers', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent('<table><tbody><tr><td class="selection-cell">Select</td><td>Supplier</td><td>1460.00</td><td>RUB</td><td>3</td><td>10</td><td class="actions">Action</td></tr></tbody></table>')
    const row = page.locator('tr')
    const widget = { type: 'ui.table', inputs: { columns: ['supplier', 'price', 'currency', 'lead', 'minimum'].map(key => ({ key })) } }
    assert.equal(visibleQuoteNumber(await quoteFieldCell(row, widget, 'price').innerText()), 1460)
    assert.equal(visibleQuoteNumber(await quoteFieldCell(row, widget, 'lead').innerText()), 3)
    assert.notEqual(visibleQuoteNumber(await quoteFieldCell(row, widget, 'minimum').innerText()), 3)
    assert.throws(() => quoteFieldCell(row, widget, 'hidden'), /No public quote column/)
  } finally { await browser.close() }
})
