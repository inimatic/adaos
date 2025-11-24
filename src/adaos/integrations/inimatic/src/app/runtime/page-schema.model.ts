export type WidgetType =
  | 'collection.grid'
  | 'visual.metricTile'
  | 'feedback.log'
  | 'overlay.modal'
  | 'input.commandBar'
  | 'desktop.widgets'

export interface PageSchema {
  id: string
  title?: string
  layout: {
    type: 'single' | 'split' | 'custom'
    areas: Array<{
      id: string
      role?: string
      label?: string
    }>
  }
  widgets: WidgetConfig[]
}

export interface WidgetConfig {
  id: string
  type: WidgetType
  area: string
  title?: string
  dataSource?: DataSourceConfig
  inputs?: Record<string, any>
  actions?: ActionConfig[]
  visibleIf?: string
}

export type DataSourceConfig = SkillDataSource | ApiDataSource | StaticDataSource
  | YDocDataSource

export interface SkillDataSource {
  kind: 'skill'
  name: string
  params?: Record<string, any>
}

export interface ApiDataSource {
  kind: 'api'
  url: string
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  params?: Record<string, any>
  body?: any
}

export interface StaticDataSource {
  kind: 'static'
  value: any
}

export interface YDocDataSource {
  kind: 'y'
  path?: string
  transform?: 'desktop.icons' | 'desktop.widgets'
}

export interface ActionConfig {
  on: string
  type: 'callSkill' | 'updateState' | 'openOverlay' | 'openModal'
  target?: string
  params?: Record<string, any>
}
