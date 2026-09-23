// This checks source identity, not DOM order or visual layout quality.
export function canonicalWidgetSources(widgets) {
  return widgets.map(widget => ({
    id: widget.id,
    type: widget.type,
    area: widget.area ?? null,
    dataSource: widget.dataSource ?? null,
  })).sort((left, right) => String(left.id).localeCompare(String(right.id)))
}

// Node-owned sources are resolved while a Webspace is materialized.  The
// package remains authoritative for source semantics, while nodeId and the
// matching `scope: node` are transport bindings derived from the selected
// runtime.  Ignore only those derived fields when they were not pinned by the
// package; all other rendered-source additions and mutations remain visible.
export function canonicalRenderedWidgetSources(widgets, pinnedWidgets) {
  const pinnedById = new Map(canonicalWidgetSources(pinnedWidgets).map(widget => [widget.id, widget]))
  const rendered = canonicalWidgetSources(widgets)
  for (const widget of rendered) {
    const pinned = pinnedById.get(widget.id)
    const source = widget.dataSource
    const pinnedSource = pinned?.dataSource
    if (!source || typeof source !== 'object' || Array.isArray(source)
      || !pinnedSource || typeof pinnedSource !== 'object' || Array.isArray(pinnedSource)) continue
    if (!Object.hasOwn(pinnedSource, 'nodeId')) delete source.nodeId
    if (!Object.hasOwn(pinnedSource, 'node_id')) delete source.node_id
    if (!Object.hasOwn(pinnedSource, 'scope') && source.scope === 'node') delete source.scope
  }
  return rendered
}
