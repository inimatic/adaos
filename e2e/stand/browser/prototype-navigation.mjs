import { expect } from '@playwright/test'
const overlayCallbacks = new WeakMap()

// Release notices belong to the surrounding Desktop, not to the isolated
// Prototype under review. They may arrive after first paint and must not make
// an otherwise deterministic probe depend on a user's notification history.
export async function installSystemOverlayDismissal(page, onDismiss = () => {}) {
  overlayCallbacks.set(page, onDismiss)
  await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
    const close = page.locator('.component-updates-panel__tools button[aria-label="Close"], .component-updates-panel__tools button').last()
    await close.click()
    onDismiss('component-updates')
  })
}

// Screenshot/evaluate do not necessarily invoke locator handlers. Explicitly
// close this unrelated system surface before collecting visual evidence too.
export async function dismissSystemOverlays(page) {
  const callback = overlayCallbacks.get(page)
  const backdrop = page.locator('.component-updates-backdrop')
  // The automatic handler would close the overlay BEFORE this explicit click,
  // leaving Playwright waiting for a Close button that no longer exists.
  if (callback) await page.removeLocatorHandler(backdrop)
  try {
    if (await backdrop.isVisible()) {
      await page.locator('.component-updates-panel__tools button[aria-label="Close"], .component-updates-panel__tools button').last().click()
      await expect(backdrop).toBeHidden()
      callback?.('component-updates')
    }
  } finally {
    if (callback && !page.isClosed()) await installSystemOverlayDismissal(page, callback)
  }
}

// Follow public tab controls, never mutate application state to reveal a fixture.
export async function revealPrototypeWidget(page, widgets, target) {
  const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
  const targetVisible = async () => {
    if (!await host(target.id).isVisible()) return false
    return host(target.id).evaluate(element => {
      const region = element.closest('ada-layout-region.is-compact')
      return !region || !['drawer', 'sheet', 'route', 'overflow'].includes(region.getAttribute('data-compact-presentation'))
        || region.classList.contains('is-open')
    })
  }
  const disclose = async () => {
    const controls = page.locator('.layout-region-trigger')
    const count = await controls.count()
    if (count > 8) throw new Error('Unexpected number of workbench disclosure controls')
    for (let index = 0; index < count; index++) {
      if (!await controls.nth(index).isVisible()) continue
      await controls.nth(index).click()
      try {
        await expect.poll(targetVisible, { timeout: 1500 }).toBe(true)
        return true
      } catch (error) { if (page.isClosed()) throw error }
      const close = page.locator('ada-layout-region.is-compact.is-open .layout-region__header button[aria-label="Close panel"]:visible')
      if (await close.count()) await close.last().click()
      else if (await page.locator('.drawer-backdrop').isVisible()) {
        await page.locator('.drawer-backdrop').click({ position: { x: 2, y: 2 } })
        await expect(page.locator('.drawer-backdrop')).toBeHidden()
      }
    }
    return false
  }
  // CSS-visible main content may still be covered by a previously disclosed
  // supporting sheet. Return through its close control before selecting rows.
  const openPanels = page.locator('ada-layout-region.is-compact.is-open')
  for (let index = await openPanels.count() - 1; index >= 0; index--) {
    const panel = openPanels.nth(index)
    if (await panel.locator(`[data-webui-widget-id=${JSON.stringify(target.id)}]`).count()) continue
    const close = panel.locator('.layout-region__header button[aria-label="Close panel"]')
    if (await close.isVisible()) await close.click()
  }
  if (await targetVisible()) return
  // Search panels in the CURRENT tab before changing its selection. A section
  // can require both a tab and a sheet, not one independent navigation control.
  if (await disclose()) return
  for (const navigation of widgets.filter(widget => widget.inputs?.variant === 'tabs')) {
    if (!await host(navigation.id).isVisible()) continue
    for (const button of navigation.inputs.buttons || []) {
      const actions = navigation.actions?.filter(action => action.on === `click:${button.id}`) || []
      if (!actions.length || actions.some(action => action.type !== 'updateState')) continue
      const control = host(navigation.id).locator(`[data-command-id=${JSON.stringify(button.id)}]`)
      if (!await control.isVisible() || !await control.isEnabled()) continue
      await control.click()
      try {
        await expect.poll(targetVisible, { timeout: 1500 }).toBe(true)
        return
      } catch (error) {
        if (page.isClosed()) throw error
      }
      if (await disclose()) return
    }
  }
  // Compact workbenches disclose supporting regions through public panel controls.
  if (await disclose()) return
  // A focus-detail workbench may expose its compact supporting section only
  // after a record is selected. Follow a bounded, read-only public selection;
  // never run arbitrary row commands or inject state to make a probe pass.
  const selectable = widgets.filter(widget => ['ui.table', 'ui.list', 'collection.board'].includes(widget.type)
    && widget.inputs?.selectionMode && widget.inputs.selectionMode !== 'none'
    && ['select', 'open-detail'].includes(widget.inputs.rowActivation)
    && (widget.actions || []).filter(action => action.on === 'select').every(action => action.type === 'updateState'))
  if (selectable.length > 8) throw new Error('Unexpected number of selectable disclosure collections')
  for (const widget of selectable) {
    if (!await host(widget.id).isVisible()) continue
    const rows = host(widget.id).locator(prototypeRowSelector(widget))
    // Disclosure must not silently replace a record the caller just selected.
    // Activating that existing selection opens the compact supporting surface.
    const selectedIndex = await rows.evaluateAll(elements => elements.findIndex(element =>
      element.getAttribute('aria-selected') === 'true' || element.classList.contains('row-selected')
      || element.querySelector('.selection-marker[aria-pressed="true"]')))
    const row = rows.nth(selectedIndex >= 0 ? selectedIndex : 0)
    // Materialization readiness precedes asynchronous collection queries.
    // An initial empty placeholder is not evidence that no record is reachable.
    try { await expect(row).toBeVisible({ timeout: 5000 }) }
    catch (error) { if (page.isClosed()) throw error; else continue }
    await selectPrototypeRow(row, { activate: widget.inputs?.rowActivation === 'open-detail' })
    try {
      await expect.poll(targetVisible, { timeout: 1500 }).toBe(true)
      return
    } catch (error) {
      if (page.isClosed()) throw error
    }
    if (await disclose()) return
    const close = page.locator('ada-layout-region.is-compact.is-open .layout-region__header button[aria-label="Close panel"]:visible')
    if (await close.count()) await close.last().click()
  }
  throw new Error(`No reachable section for ${target.id}`)
}

// Create commands belong to collection headers; they are never record openers.
// Row editor actions remain public controls even when selection is disabled.
export function recordEditorOpener(widgets, modalId, selectedStateKey) {
  for (const owner of widgets) {
    const rowButtonIds = new Set((owner.inputs?.buttons || []).map(button => String(button.id)))
    const collection = ['ui.table', 'ui.list', 'collection.board'].includes(owner.type)
    const action = owner.actions?.find(item => (modalId
      ? ['openModal', 'navigate'].includes(item.type) && item.params?.modalId === modalId
      : collection && selectedStateKey && item.type === 'updateState' && item.params?.[selectedStateKey] === '$event.id')
      && item.on?.startsWith('click:')
      && (!collection || rowButtonIds.has(item.on.slice('click:'.length))))
    if (action) return { owner, commandId: action.on.slice('click:'.length), collection }
  }
  return null
}

export function prototypeRowSelector(widget) {
  if (widget.type === 'collection.board') return '.board-card__main'
  // Record actions do not require row selection. Empty-state rows are not records.
  if (widget.type === 'ui.table') return 'tbody > tr:not(:has(td.empty))'
  return '.collection-focus-item'
}

// List/card renderers may put the public action rail next to the focusable
// record body. Scope the opener to that record, never the whole collection.
export function prototypeRowCommand(row, commandId) {
  const selector = `[data-command-id=${JSON.stringify(commandId)}]`
  return row.locator(selector)
    .or(row.locator('xpath=following-sibling::*[1][contains(concat(" ", normalize-space(@class), " "), " list-row-actions-below ")]').locator(selector))
    .or(row.locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " note-card ")][1]').locator('.note-card-actions').locator(selector))
    .or(row.locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " board-card ")][1]').locator(selector))
    .first()
}

export async function prototypeRowById(host, widget, recordId) {
  const rows = host.locator(prototypeRowSelector(widget))
  const index = () => rows.evaluateAll((elements, expected) => elements.findIndex(element => {
    const context = window.ng?.getContext(element)
    const item = context?.$implicit || context?.item
    return item?.id === expected || element.getAttribute('data-webui-board-item-id') === expected
      || element.closest('[data-webui-board-item-id]')?.getAttribute('data-webui-board-item-id') === expected
  }), recordId)
    await expect.poll(index, { timeout: 20_000,
      message: 'Created record must be publicly discoverable by its returned identity after asynchronous resource readiness' }).toBeGreaterThanOrEqual(0)
  return rows.nth(await index())
}

export async function selectPrototypeRow(row, { activate = false } = {}) {
  // Selection is not row activation. The public marker changes selection
  // without opening a detail sheet over an editor action on compact layouts.
  const marker = row.locator('.selection-marker').first()
  if (!activate && await marker.isVisible()) {
    await marker.click()
    return
  }
  // The geometric centre of a short row can be an edit/approve button.
  // Select through a data cell, not through an arbitrary nested action.
  const values = row.locator('td .cell-text')
  for (let index = 0; index < await values.count(); index++) {
    if (!await values.nth(index).isVisible()) continue
    await values.nth(index).click()
    return
  }
  // Empty rows can still have a public selection marker.
  if (await marker.isVisible()) {
    await marker.click()
    return
  }
  await row.click({ position: { x: 12, y: 12 } })
}

export async function selectPrototypeRowForCommand(page, widgets, collection, row) {
  await selectPrototypeRow(row)
  // A row-accent collection has no selection-only marker: a public cell click
  // legitimately opens its compact detail route. Return through public Close
  // before attempting a row command, preserving the selected record identity.
  await revealPrototypeWidget(page, widgets, collection)
}

export async function selectPrototypeFormOption(formHost, field, value) {
  // Angular ngValue encodes DOM option values; radios need not have a value
  // attribute at all. Inspect the published choices, then use public controls.
  const options = () => formHost.evaluate((element, fieldId) => {
    const component = window.ng?.getComponent(element.querySelector('ada-form-widget'))
    const control = component?.fields?.find(item => item.id === fieldId)
    return control ? component.choiceOptions(control) : []
  }, field.id)
  await expect.poll(async () => (await options()).some(option => option.value === value)).toBe(true)
  const index = (await options()).findIndex(option => option.value === value)
  const container = formHost.locator(`[data-webui-field-id=${JSON.stringify(field.id)}]`)
  if (await container.locator('select').count()) await container.locator('select').selectOption({ index: index + 1 })
  else await container.locator('input[type=radio]').nth(index).check()
}
