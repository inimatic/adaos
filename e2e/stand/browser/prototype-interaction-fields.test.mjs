import assert from 'node:assert/strict'
import test from 'node:test'
import { pickEditableInteractionField } from './prototype-interaction-fields.mjs'

const fields = [{ id: 'parent', type: 'dropdown' }, { id: 'quantity', type: 'number' }]
const update = { params: { payload: { parent: '$event.values.parent', quantity: '$event.values.quantity' } } }

test('a quantity edit does not unintentionally reparent a line before persistence verification', () => {
  assert.equal(pickEditableInteractionField(fields, update).id, 'quantity')
})

test('an explicitly requested relationship edit is not silently replaced by a simpler field', () => {
  assert.equal(pickEditableInteractionField(fields, update, { fieldType: 'dropdown' }).id, 'parent')
  assert.equal(pickEditableInteractionField(fields, update, { fieldId: 'parent' }).id, 'parent')
})

test('read-only, conditional and unsubmitted fields are not chosen', () => {
  assert.equal(pickEditableInteractionField(fields.map(field => ({ ...field, readOnly: true })), update), undefined)
  assert.equal(pickEditableInteractionField(fields, { params: { payload: {} } }), undefined)
})
