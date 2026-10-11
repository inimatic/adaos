import { expect } from '@playwright/test'
import { prototypeRowById, revealPrototypeWidget, selectPrototypeRow } from './prototype-navigation.mjs'

export function quoteFieldCell(row, widget, field) {
  const index = widget.inputs?.columns?.findIndex(column => column.key === field) ?? -1
  if (widget.type !== 'ui.table' || index < 0) throw new Error(`No public quote column for ${field}`)
  return row.locator('td:not(.selection-cell):not(.actions)').nth(index)
}

export function visibleQuoteNumber(text) {
  const value = text.replace(/[\s\u00a0\u202f]/g, '')
  return /^[+-]?\d+(?:[.,]\d+)?$/.test(value) ? Number(value.replace(',', '.')) : null
}

// Discover task evidence without requiring a particular request representation.
// Every identity comes from typed seeded links, never guessed labels or DOM IDs.
export function supplierComparisonMetadata(semantic) {
  for (const resource of semantic.resources || []) {
    const choose = (pattern, numeric = false) => {
      const fields = resource.fields.filter(field => pattern.test(field.id)
        && (!numeric || (resource.records || []).some(record => Number.isFinite(record[field.id]))))
      return fields.length === 1 ? fields[0].id : null
    }
    const fields = { price: choose(/price|cost/i, true), lead: choose(/delivery|lead|days/i, true),
      supplier: choose(/supplier|vendor/i), currency: choose(/currency/i) }
    if (!Object.values(fields).every(Boolean)) continue
    for (const view of semantic.views || []) {
      const filter = view.selection_filter
      if (view.role !== 'collection' || view.resource_ref !== resource.id || !filter || filter.source_field_ref
        || !Object.values(fields).every(field => view.field_refs.includes(field))) continue
      const source = semantic.views.find(item => item.id === filter.source_view_ref && item.role === 'collection')
      const relationship = (semantic.relationships || []).find(link => link.from_resource_ref === resource.id
        && link.from_field_ref === filter.field_ref && link.to_resource_ref === source?.resource_ref && link.to_field_ref === 'id')
      if (!source || !relationship) continue
      for (const quote of resource.records || []) {
        const parentId = quote[filter.field_ref]
        if (typeof parentId !== 'string' || !parentId) continue
        const quotes = resource.records.filter(record => record[filter.field_ref] === parentId
          && Number.isFinite(record[fields.price]) && Number.isFinite(record[fields.lead])
          && typeof record[fields.supplier] === 'string' && record[fields.supplier].trim()
          && typeof record[fields.currency] === 'string' && record[fields.currency].trim())
        if (new Set(quotes.map(record => record[fields.supplier])).size < 2) continue
        let current = source
        let currentId = parentId
        const selections = []
        const seen = new Set()
        let valid = true
        while (current) {
          if (seen.has(current.id)) { valid = false; break }
          seen.add(current.id)
          const currentResource = semantic.resources.find(item => item.id === current.resource_ref)
          const record = currentResource?.records?.find(item => item.id === currentId)
          if (!record) { valid = false; break }
          selections.unshift({ viewId: current.id, recordId: currentId })
          const parentFilter = current.selection_filter
          if (!parentFilter) break
          const parent = semantic.views.find(item => item.id === parentFilter.source_view_ref && item.role === 'collection')
          const link = (semantic.relationships || []).find(item => item.from_resource_ref === current.resource_ref
            && item.from_field_ref === parentFilter.field_ref && item.to_resource_ref === parent?.resource_ref && item.to_field_ref === 'id')
          if (!parent || !link || parentFilter.source_field_ref || typeof record[parentFilter.field_ref] !== 'string') { valid = false; break }
          currentId = record[parentFilter.field_ref]
          current = parent
        }
        if (valid) return { resource, view, fields, parentField: filter.field_ref, filterValue: parentId,
          sourceViewId: source.id, selectionId: parentId, selections, quoteIds: quotes.map(record => record.id) }
      }
    }
  }
  return null
}

// Only public selection/rendering plus an independent read-only durable query.
export async function qualifyBasicSupplierComparison({ page, context, widgets, collection, basic, hub, token, scenario, sample }) {
  const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
  const selections = basic.selections || [{ viewId: basic.sourceViewId || collection?.id, recordId: basic.selectionId || basic.requestId }]
  for (const selection of selections) {
    const selectionCollection = widgets.find(widget => widget.id === selection.viewId)
    if (!selectionCollection) throw new Error('No public source collection for supplier comparison')
    await revealPrototypeWidget(page, widgets, selectionCollection)
    const selectedRow = await prototypeRowById(host(selectionCollection.id), selectionCollection, selection.recordId)
    await selectPrototypeRow(selectedRow)
  }
  const widget = widgets.find(widget => widget.id === basic.view.id)
  if (!widget) throw new Error('No public supplier comparison collection')
  await revealPrototypeWidget(page, widgets, widget)
  const response = await context.request.post(`${hub}/api/resources/query`, {
    headers: { 'X-AdaOS-Token': token }, data: { resource_type: `prototype.project.${scenario}.${basic.resource.id}`,
      filters: { [basic.parentField]: basic.filterValue || basic.requestId }, limit: 1000 },
  })
  const body = await response.json()
  const quotes = body.items || []
  const { price, lead, supplier, currency } = basic.fields
  if (!response.ok() || basic.quoteIds.some(id => !quotes.some(quote => quote.id === id))
    || new Set(quotes.map(quote => quote[supplier])).size < 2) throw new Error('Insufficient independently persisted supplier comparison')
  for (const quote of quotes) {
    if (quote[basic.parentField] !== (basic.filterValue || basic.requestId) || !Number.isFinite(quote[price]) || !Number.isFinite(quote[lead])
      || !quote[supplier] || !quote[currency]) throw new Error('Incomplete or unrelated persisted supplier quote')
    const row = await prototypeRowById(host(widget.id), widget, quote.id)
    const display = field => widget.inputs?.columns?.find(column => column.key === field)?.valueLabels?.[quote[field]] || quote[field]
    await expect(row).toContainText(String(display(supplier)))
    await expect(row).toContainText(String(display(currency)))
    for (const field of [price, lead]) {
      if (widget.type === 'ui.table') {
        const cell = quoteFieldCell(row, widget, field)
        await expect(cell).toBeVisible()
        await expect.poll(async () => visibleQuoteNumber(await cell.innerText())).toBe(quote[field])
      } else {
        const numericText = String(quote[field]).replace('.', '[.,]')
        await expect(row).toContainText(new RegExp(`(?<![\\d])${numericText}(?:[.,]0+)?(?![\\d])`), { useInnerText: true })
      }
    }
  }
  sample.checks.push({ task: 'compare-multiple-suppliers/visible-price-lead-currency/independent-persistence',
    status: 'passed', requestId: basic.requestId, itemId: basic.itemId, quoteIds: quotes.map(quote => quote.id) })
}
