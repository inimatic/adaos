// This checks source identity, not DOM order or visual layout quality.
export function canonicalWidgetSources(widgets) {
  return widgets.map(widget => ({
    id: widget.id,
    type: widget.type,
    area: widget.area ?? null,
    dataSource: widget.dataSource ?? null,
  })).sort((left, right) => String(left.id).localeCompare(String(right.id)))
}
