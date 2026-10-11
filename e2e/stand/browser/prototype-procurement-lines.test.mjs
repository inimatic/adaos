import assert from 'node:assert/strict'
import test from 'node:test'
import { chromium } from '@playwright/test'
import { activeGuardRequiredFields, fillAdditionalRequiredLineFields } from './prototype-procurement-lines.mjs'

test('only active canonical guard requirements become conditional inputs', () => {
  const guard = { when: { field_ref: 'request.id', operator: 'nonempty' }, require_nonempty: ['unit'] }
  assert.deepEqual(activeGuardRequiredFields(guard, { 'request.id': 'new-request' }), ['unit'])
  assert.deepEqual(activeGuardRequiredFields(guard, { 'request.id': '' }), [])
  assert.deepEqual(activeGuardRequiredFields({ ...guard, when: { field_ref: 'amount', operator: 'equals', value: 0 } }, { amount: 0 }), ['unit'])
  assert.deepEqual(activeGuardRequiredFields({ ...guard, when: { field_ref: 'amount', operator: 'equals', value: 0 } }, { amount: '0' }), [])
  assert.deepEqual(activeGuardRequiredFields(guard, { 'request.id': [] }), [])
  assert.deepEqual(activeGuardRequiredFields(guard, { 'request.id': 0 }), ['unit'])
})

test('an active conditional unit is filled even when the resource field is optional', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent('<form><div data-webui-field-id="unit"><input type="radio" value="kg"></div><div data-webui-field-id="note"><input></div></form>')
    const fields = [{ id: 'unit', type: 'singleChoice', required: false }, { id: 'note', type: 'shortText', required: false }]
    const payload = { unit: '$event.values.unit', note: '$event.values.note' }
    await fillAdditionalRequiredLineFields(page.locator('form'), { inputs: { fields } }, { params: { payload } }, { requiredFieldIds: ['unit'] })
    assert.equal(await page.locator('input:checked').inputValue(), 'kg')
    assert.equal(await page.locator('[data-webui-field-id="note"] input').inputValue(), '')
  } finally { await browser.close() }
})

test('additional required line choices are populated without changing asserted composition or optional fields', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<form>
      <div data-webui-field-id="quantity"><input value="7"></div>
      <div data-webui-field-id="unit"><input type="radio" name="unit" value="kg"><input type="radio" name="unit" value="pcs"></div>
      <div data-webui-field-id="status"><select><option value="">Choose</option><option value="draft">Draft</option></select></div>
      <div data-webui-field-id="optional"><input></div>
      <div data-webui-field-id="readonly"><input value="original"></div>
      <div data-webui-field-id="unsubmitted"><input></div>
    </form>`)
    const fields = [{ id: 'quantity', type: 'number', required: true }, { id: 'unit', type: 'singleChoice', required: true },
      { id: 'status', type: 'dropdown', required: true }, { id: 'optional', type: 'shortText', required: false },
      { id: 'readonly', type: 'shortText', required: true, readOnly: true }, { id: 'unsubmitted', type: 'shortText', required: true }]
    const payload = Object.fromEntries(fields.filter(field => field.id !== 'unsubmitted').map(field => [field.id, `$event.values.${field.id}`]))
    await fillAdditionalRequiredLineFields(page.locator('form'), { inputs: { fields } }, { params: { payload } }, { skipFieldIds: ['quantity'] })
    assert.equal(await page.locator('[data-webui-field-id="quantity"] input').inputValue(), '7')
    assert.equal(await page.locator('[data-webui-field-id="unit"] input:checked').inputValue(), 'kg')
    assert.equal(await page.locator('[data-webui-field-id="status"] select').inputValue(), 'draft')
    assert.equal(await page.locator('[data-webui-field-id="optional"] input').inputValue(), '')
    assert.equal(await page.locator('[data-webui-field-id="readonly"] input').inputValue(), 'original')
    assert.equal(await page.locator('[data-webui-field-id="unsubmitted"] input').inputValue(), '')
  } finally { await browser.close() }
})
