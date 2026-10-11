import assert from 'node:assert/strict'
import test from 'node:test'
import { chromium } from '@playwright/test'
import { dismissSystemOverlays, installSystemOverlayDismissal, prototypeRowCommand, prototypeRowSelector, recordEditorOpener, revealPrototypeWidget, selectPrototypeFormOption, selectPrototypeRow, selectPrototypeRowForCommand } from './prototype-navigation.mjs'

test('explicit dismissal cannot race its installed automatic overlay handler', async () => {
  const browser=await chromium.launch({headless:true})
  try {
    const page=await browser.newPage()
    await page.setContent(`<div class="component-updates-backdrop"><div class="component-updates-panel__tools"><button aria-label="Close" onclick="document.querySelector('.component-updates-backdrop').remove()">Close</button></div></div>`)
    let dismissed=0
    await installSystemOverlayDismissal(page,()=>dismissed++)
    await dismissSystemOverlays(page)
    assert.equal(dismissed,1)
    assert.equal(await page.locator('.component-updates-backdrop').count(),0)
    await dismissSystemOverlays(page)
    assert.equal(dismissed,1)
  }finally{await browser.close()}
})

test('a create header action is not a row editor action', () => {
  const owner = { id: 'requests', type: 'ui.table', inputs: {
    headerActions: [{ id: 'create_request' }], buttons: [{ id: 'open_record' }],
  }, actions: [
    { on: 'click:create_request', type: 'openModal', params: { modalId: 'editor' } },
    { on: 'click:open_record', type: 'openModal', params: { modalId: 'editor' } },
  ] }
  assert.equal(recordEditorOpener([owner], 'editor').commandId, 'open_record')
})

test('a create-only modal is not exposed as an editable existing record', () => {
  const owner = { type: 'ui.table', inputs: { headerActions: [{ id: 'create' }] },
    actions: [{ on: 'click:create', type: 'openModal', params: { modalId: 'editor' } }] }
  assert.equal(recordEditorOpener([owner], 'editor'), null)
})

test('an inline editor uses the row command even when the collection has no selection mode', () => {
  const owner = { id: 'lines', type: 'ui.list', inputs: {
    headerActions: [{ id: 'add' }], buttons: [{ id: 'edit' }], selectionMode: 'none',
  }, actions: [
    { on: 'click:add', type: 'updateState', params: { selected_line: '' } },
    { on: 'click:edit', type: 'updateState', params: { selected_line: '$event.id' } },
  ] }
  assert.equal(recordEditorOpener([owner], undefined, 'selected_line').commandId, 'edit')
  assert.equal(recordEditorOpener([owner], undefined, 'another_record'), null)
  assert.equal(recordEditorOpener([owner], undefined), null)
})

test('record-row lookup is independent of table selection and excludes empty placeholders', () => {
  assert.equal(prototypeRowSelector({ type: 'ui.table' }), 'tbody > tr:not(:has(td.empty))')
  assert.equal(prototypeRowSelector({ type: 'ui.list' }), '.collection-focus-item')
  assert.equal(prototypeRowSelector({ type: 'collection.board' }), '.board-card__main')
})

test('public actions adjacent to list and card bodies stay scoped to their record', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`
      <ion-item class="collection-focus-item">First</ion-item><div class="list-row-actions-below"><button data-command-id="edit" data-record="one">Edit</button></div>
      <ion-item class="collection-focus-item">Second</ion-item><div class="list-row-actions-below"><button data-command-id="edit" data-record="two">Edit</button></div>
      <article class="note-card"><button class="collection-focus-item">Third</button><div class="note-card-actions"><button data-command-id="edit" data-record="three">Edit</button></div></article>`)
    assert.equal(await prototypeRowCommand(page.locator('.collection-focus-item').nth(1), 'edit').getAttribute('data-record'), 'two')
    assert.equal(await prototypeRowCommand(page.locator('.collection-focus-item').nth(2), 'edit').getAttribute('data-record'), 'three')
  } finally { await browser.close() }
})

test('empty autonumber cells do not prevent public record selection or invoke actions', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<table><tbody>
      <tr onclick="this.dataset.selected='yes'"><td><span class="cell-text"></span></td><td><span class="cell-text">Visible</span></td><td><button onclick="throw Error('Action must not run')">Approve</button></td></tr>
      <tr><td><button class="selection-marker" onclick="this.closest('tr').dataset.selected='yes'">Select</button></td><td><span class="cell-text"></span></td></tr>
    </tbody></table>`)
    for (const row of [page.locator('tr').nth(0), page.locator('tr').nth(1)]) {
      await selectPrototypeRow(row)
      assert.equal(await row.getAttribute('data-selected'), 'yes')
    }
  } finally { await browser.close() }
})

test('selection uses the marker without activating a detail overlay; activation is explicit', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<table><tbody><tr onclick="this.dataset.activated='yes'">
      <td><button class="selection-marker" onclick="event.stopPropagation();this.closest('tr').dataset.selected='yes'">Select</button></td>
      <td><span class="cell-text">Record</span></td></tr></tbody></table>`)
    const row = page.locator('tr')
    await selectPrototypeRow(row)
    assert.equal(await row.getAttribute('data-selected'), 'yes')
    assert.equal(await row.getAttribute('data-activated'), null)
    await selectPrototypeRow(row, { activate: true })
    assert.equal(await row.getAttribute('data-activated'), 'yes')
  } finally { await browser.close() }
})

test('compact focus-detail disclosure may require public selection before any panel trigger exists', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<div data-webui-widget-id="stock"><table><tbody><tr onclick="document.querySelector('[data-webui-widget-id=requests]').hidden=false"><td><span class="cell-text">Stock item</span></td></tr></tbody></table></div>
      <div data-webui-widget-id="requests" hidden>Requests</div>`)
    await revealPrototypeWidget(page, [
      { id: 'stock', type: 'ui.table', inputs: { selectionMode: 'single', rowActivation: 'select' } },
    ], { id: 'requests' })
    assert.equal(await page.locator('[data-webui-widget-id=requests]').isVisible(), true)
  } finally { await browser.close() }
})

test('record activation waits for an asynchronous collection and does not substitute selection-only radio clicks', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<div data-webui-widget-id="stock"><h2>Stock</h2><table><tbody></tbody></table></div><div data-webui-widget-id="requests" hidden>Requests</div>`)
    await page.evaluate(() => setTimeout(() => {
      document.querySelector('tbody').innerHTML = `<tr onclick="document.querySelector('[data-webui-widget-id=requests]').hidden=false"><td><button class="selection-marker" onclick="event.stopPropagation()">Select only</button></td><td><span class="cell-text">Activate record</span></td></tr>`
    }, 100))
    await revealPrototypeWidget(page, [{ id: 'stock', type: 'ui.table',
      inputs: { selectionMode: 'single', rowActivation: 'open-detail' }, actions: [{ on: 'select', type: 'updateState' }] }], { id: 'requests' })
    assert.equal(await page.locator('[data-webui-widget-id=requests]').isVisible(), true)
  } finally { await browser.close() }
})

test('public choice selection respects Angular ngValue and radios without value attributes', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<div id="form"><ada-form-widget></ada-form-widget>
      <div data-webui-field-id="item"><select><option value="">Select</option><option value="1: other">Other</option><option value="2: chosen">Chosen</option></select></div>
      <div data-webui-field-id="currency"><input type="radio" name="currency"><input type="radio" name="currency"></div></div>`)
    await page.evaluate(() => { window.ng = { getComponent: () => ({ fields: [{ id: 'item' }, { id: 'currency' }],
      choiceOptions: field => (field.id === 'item' ? ['other', 'chosen'] : ['USD', 'RUB']).map(value => ({ value })) }) } })
    await selectPrototypeFormOption(page.locator('#form'), { id: 'item' }, 'chosen')
    assert.equal(await page.locator('select').inputValue(), '2: chosen')
    await selectPrototypeFormOption(page.locator('#form'), { id: 'currency' }, 'RUB')
    assert.equal(await page.locator('input[type=radio]').nth(1).isChecked(), true)
  } finally { await browser.close() }
})

test('compact disclosure searches a panel inside each tab, not only after the final tab', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<div data-webui-widget-id="tabs">
      <button data-command-id="inventory" onclick="document.querySelector('#history').hidden=false">Inventory</button>
      <button data-command-id="requests" onclick="document.querySelector('#history').hidden=true">Requests</button></div>
      <button class="layout-region-trigger" onclick="document.querySelector('#panel').classList.add('is-open');document.querySelector('#panel').hidden=false">Detail</button>
      <ada-layout-region id="panel" class="is-compact" data-compact-presentation="sheet" hidden>
        <header class="layout-region__header"><button aria-label="Close panel" onclick="document.querySelector('#panel').classList.remove('is-open');document.querySelector('#panel').hidden=true">Close</button></header>
        <div id="history" data-webui-widget-id="history" hidden>Movement history</div></ada-layout-region>`)
    await revealPrototypeWidget(page, [{ id: 'tabs', inputs: { variant: 'tabs', buttons: [{ id: 'inventory' }, { id: 'requests' }] },
      actions: [{ on: 'click:inventory', type: 'updateState' }, { on: 'click:requests', type: 'updateState' }] }], { id: 'history' })
    assert.equal(await page.locator('#history').isVisible(), true)
    assert.equal(await page.locator('#panel').getAttribute('class'), 'is-compact is-open')
    await revealPrototypeWidget(page, [], { id: 'tabs' })
    assert.equal(await page.locator('#panel').isVisible(), false)
    assert.equal(await page.locator('[data-command-id=requests]').isVisible(), true)
  } finally { await browser.close() }
})

test('a closing sheet is not reachable merely because CSS visibility is still transitioning', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<button class="layout-region-trigger" onclick="document.querySelector('#panel').classList.add('is-open')">Open detail</button>
      <ada-layout-region id="panel" class="is-compact" data-compact-presentation="sheet">
        <div data-webui-widget-id="lines">Lines</div></ada-layout-region>`)
    // This deliberately remains CSS-visible while logically closed, as during
    // the real sheet's visibility transition. Only its public trigger opens it.
    await revealPrototypeWidget(page, [], { id: 'lines' })
    assert.equal(await page.locator('#panel').getAttribute('class'), 'is-compact is-open')
  } finally { await browser.close() }
})

test('compact selection-based disclosure preserves the already selected second record', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<div data-webui-widget-id="tickets"><table><tbody>
      <tr aria-selected="false" onclick="window.selected='first';document.querySelector('#history').hidden=false"><td><span class="cell-text">First</span></td></tr>
      <tr aria-selected="true" onclick="window.selected='second';document.querySelector('#history').hidden=false"><td><span class="cell-text">Second</span></td></tr>
      </tbody></table></div><div data-webui-widget-id="history" id="history" hidden>Comments</div>`)
    await page.evaluate(() => { window.selected = 'second' })
    await revealPrototypeWidget(page, [{ id: 'tickets', type: 'ui.table', inputs: { selectionMode: 'single', rowActivation: 'open-detail' },
      actions: [{ on: 'select', type: 'updateState' }] }], { id: 'history' })
    assert.equal(await page.evaluate(() => window.selected), 'second')
    assert.equal(await page.locator('#history').isVisible(), true)
  } finally { await browser.close() }
})

test('disclosure never clicks a logically closed panel during its visibility transition', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<button class="layout-region-trigger">Auto-closed empty panel</button>
      <button class="layout-region-trigger" onclick="document.querySelector('#target').classList.add('is-open');document.querySelector('#target').hidden=false">Target panel</button>
      <ada-layout-region class="is-compact" data-compact-presentation="sheet">
        <header class="layout-region__header"><button aria-label="Close panel" onclick="window.incorrectClose=(window.incorrectClose||0)+1">Stale close</button></header></ada-layout-region>
      <ada-layout-region id="target" class="is-compact" data-compact-presentation="sheet" hidden>
        <div data-webui-widget-id="editor">Editor</div></ada-layout-region>`)
    await revealPrototypeWidget(page, [], { id: 'editor' })
    assert.equal(await page.evaluate(() => window.incorrectClose || 0), 0)
  } finally { await browser.close() }
})

test('row-accent selection closes its public detail route before a row command, preserving identity', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent(`<div data-webui-widget-id="tickets"><table><tbody><tr onclick="window.selected='ticket-two';document.querySelector('#panel').classList.add('is-open')">
      <td><span class="cell-text">Ticket two</span></td><td class="actions"><button id="edit" onclick="event.stopPropagation();window.edited=window.selected">Edit</button></td></tr></tbody></table></div>
      <ada-layout-region id="panel" class="is-compact" data-compact-presentation="route" style="position:fixed;inset:0;background:white">
        <header class="layout-region__header"><button aria-label="Close panel" onclick="document.querySelector('#panel').classList.remove('is-open')">Close</button></header>
        <div data-webui-widget-id="detail">Details</div></ada-layout-region>
      <style>ada-layout-region:not(.is-open){display:none}</style>`)
    const collection = { id: 'tickets', type: 'ui.table', inputs: { selectionMode: 'single', rowActivation: 'open-detail' } }
    await selectPrototypeRowForCommand(page, [collection], collection, page.locator('tr'))
    await page.locator('#edit').click()
    assert.equal(await page.evaluate(() => window.selected), 'ticket-two')
    assert.equal(await page.evaluate(() => window.edited), 'ticket-two')
    assert.equal(await page.locator('#panel').isVisible(), false)
  } finally { await browser.close() }
})
