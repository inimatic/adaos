export type WebUiPrimitiveType =
  | 'string'
  | 'number'
  | 'integer'
  | 'boolean'
  | 'array'
  | 'object'
  | 'any'

export type WebUiDiagnosticLevel = 'error' | 'warning' | 'info'

export interface WebUiParamSchema {
  type?: WebUiPrimitiveType | string
  required?: boolean
  default?: unknown
  enum?: readonly unknown[]
}

export interface WebUiSkillView {
  title?: string
  surface?: string | readonly string[]
  surfaces?: readonly string[]
  params?: Record<string, WebUiParamSchema>
  data?: Record<string, unknown>
  [key: string]: unknown
}

export interface WebUiSkillInterface {
  schema?: 'adaos.ui.skill_interface.v1' | string
  defaultView?: string
  views?: Record<string, WebUiSkillView>
  transitions?: readonly WebUiSkillTransition[]
}

export interface WebUiSkillTransition {
  from?: string
  on?: string
  to: string
  surface?: string
  params?: Record<string, unknown>
  [key: string]: unknown
}

export interface WebUiManifest {
  schema?: 'adaos.webui.v1' | string
  ui?: WebUiRoot
  catalog?: Record<string, unknown>
  apps?: readonly unknown[]
  widgets?: readonly WebUiWidgetConfig[]
  registry?: Record<string, unknown>
  resources?: Record<string, unknown>
  nlu?: Record<string, unknown>
  llm_hints?: Record<string, unknown>
  nlu_hints?: Record<string, unknown>
  [key: string]: unknown
}

export interface WebUiRoot {
  application?: WebUiApplication
  [key: string]: unknown
}

export interface WebUiApplication {
  version?: string
  desktop?: WebUiDesktopApplication
  [key: string]: unknown
}

export interface WebUiDesktopApplication {
  pageSchema?: WebUiPageSchema
  [key: string]: unknown
}

export interface WebUiPageSchema {
  id: string
  title?: string
  layout: Record<string, unknown>
  widgets: readonly WebUiWidgetConfig[]
  initialState?: Record<string, unknown>
  autoActions?: readonly unknown[]
  semantic?: Record<string, unknown>
  [key: string]: unknown
}

export interface WebUiWidgetConfig {
  id: string
  type: string
  area?: string
  title?: string
  dataSource?: Record<string, unknown>
  inputs?: Record<string, unknown>
  actions?: readonly unknown[]
  [key: string]: unknown
}

export interface WebUiModalAddress {
  route?: string
  view?: string
  params?: Record<string, unknown>
}

export interface WebUiModalRoute {
  view?: string
  title?: string
  params?: Record<string, WebUiParamSchema>
  state?: Record<string, unknown>
  data?: Record<string, unknown>
  [key: string]: unknown
}

export type WebUiModalDomainStateKind = 'collection' | 'entity' | 'draft' | 'custom'

export interface WebUiModalDomainEntity {
  type?: string
  idParam?: string
  idStateKey?: string
  draft?: boolean
  [key: string]: unknown
}

export interface WebUiModalDomainState {
  kind?: WebUiModalDomainStateKind
  route?: string
  view?: string
  entity?: WebUiModalDomainEntity
  state?: Record<string, unknown>
  persistence?: Record<string, unknown>
  [key: string]: unknown
}

export interface WebUiModalDomainContract {
  schema?: 'adaos.ui.modal_domain.v1' | string
  defaultState?: string
  stateKey?: string
  states?: Record<string, WebUiModalDomainState>
  [key: string]: unknown
}

export interface WebUiOwnershipSection {
  owner?: string
  scope?: string
  store?: string
  projection?: string
  keys?: readonly string[]
  ack?: string
  durability?: string
  [key: string]: unknown
}

export interface WebUiOwnershipContract {
  schema?: 'adaos.ui.state_ownership.v1' | string
  domainState?: WebUiOwnershipSection
  routeState?: WebUiOwnershipSection
  viewState?: WebUiOwnershipSection
  persistence?: WebUiOwnershipSection
  [key: string]: unknown
}

export interface WebUiModalHistoryContract {
  url?: boolean
  deepLink?: boolean
  mode?: 'push' | 'replace'
  queryPrefix?: string
  [key: string]: unknown
}

export interface WebUiModalInterface {
  schema?: 'adaos.ui.modal.interface.v1' | string
  defaultRoute?: string
  domain?: WebUiModalDomainContract
  ownership?: WebUiOwnershipContract
  history?: WebUiModalHistoryContract
  routes?: Record<string, WebUiModalRoute>
  [key: string]: unknown
}

export interface WebUiContractIssue {
  level: WebUiDiagnosticLevel | string
  code: string
  message: string
  where: string
  skill_id?: string
  source?: string
  modal_id?: string
  view_id?: string
  route_id?: string
}

export interface WebUiDiagnosticCatalogEntry {
  severity: WebUiDiagnosticLevel | string
  owner: 'skill' | 'runtime' | 'browser' | string
  remediation: string
}

export type WebUiDiagnosticCatalog = Record<string, WebUiDiagnosticCatalogEntry>

export interface WebUiContractDiagnosticsPayload {
  ok: boolean
  schema: 'adaos.webui.contract_diagnostics.v1' | string
  webspace_id: string
  status: 'valid' | 'invalid' | 'missing' | 'unavailable' | string
  source: string
  materialized: boolean
  error_count: number
  warning_count: number
  issue_count: number
  issues: readonly WebUiContractIssue[]
  summary: {
    status: string
    error_count: number
    warning_count: number
    issue_count: number
  }
  catalog?: WebUiDiagnosticCatalog
}
