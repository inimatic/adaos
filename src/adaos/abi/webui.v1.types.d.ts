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
  layout: WebUiLayout
  widgets: readonly WebUiWidgetConfig[]
  initialState?: Record<string, unknown>
  autoActions?: readonly unknown[]
  semantic?: Record<string, unknown>
  [key: string]: unknown
}

export interface WebUiAction {
  on?: string
  id?: string
  label?: string
  icon?: string
  enabledIf?: string
  type: string
  target?: string
  params?: Record<string, unknown>
  allowOffline?: boolean
  feedback?: Record<string, unknown>
  [key: string]: unknown
}

export type WebUiStateMutationOperation =
  | { op: 'set'; path: string; value?: unknown }
  | { op: 'toggle'; path: string }
  | { op: 'toggleArrayItem'; path: string; value: unknown }
  | { op: 'increment'; path: string; amount?: unknown; min?: unknown; max?: unknown; removeWhenZero?: boolean }
  | { op: 'remove'; path: string }

export interface WebUiDeclarativeExpression {
  kind: 'expression'
  op: 'add' | 'subtract' | 'multiply' | 'divide' | 'min' | 'max' | 'round' | 'equals' | 'gt' | 'gte' | 'lt' | 'lte' | 'and' | 'or' | 'not' | 'if' | 'count' | 'formatNumber'
  args?: readonly unknown[]
  value?: unknown
  condition?: unknown
  then?: unknown
  else?: unknown
  digits?: unknown
  locale?: unknown
  style?: 'decimal' | 'currency'
  currency?: string
}

export type WebUiWidgetType =
  | 'collection.grid'
  | 'collection.tree'
  | 'visual.taigaCollectionGrid'
  | 'visual.taigaMetricChart'
  | 'visual.taigaTree'
  | 'visual.frameViewer'
  | 'visual.serviceFrame'
  | 'visual.image'
  | 'visual.metricTile'
  | 'visual.metricChart'
  | 'visual.timeseriesChart'
  | 'visual.qrCode'
  | 'feedback.log'
  | 'feedback.statusBar'
  | 'static.markdown'
  | 'ui.chat'
  | 'ui.voiceInput'
  | 'ui.voiceDebug'
  | 'ui.list'
  | 'ui.table'
  | 'ui.form'
  | 'ui.actions'
  | 'ui.jsonViewer'
  | 'item.textEditor'
  | 'item.codeViewer'
  | 'item.details'
  | 'input.commandBar'
  | 'input.fileUpload'
  | 'input.frameSlider'
  | 'input.text'
  | 'input.selector'
  | 'desktop.widgets'
  | 'media.videoBrowser'
  | 'media.cvCamera'
  | 'host.cvRuntime'
  | 'host.webspaceControls'

export type WebUiFormInputType =
  | 'shortText'
  | 'short_text'
  | 'shortAnswer'
  | 'short_answer'
  | 'text'
  | 'longText'
  | 'long_text'
  | 'longAnswer'
  | 'long_answer'
  | 'paragraph'
  | 'textarea'
  | 'number'
  | 'integer'
  | 'email'
  | 'url'
  | 'phone'
  | 'password'
  | 'pin'
  | 'date'
  | 'time'
  | 'dateTime'
  | 'date_time'
  | 'datetime'
  | 'dateRange'
  | 'date_range'
  | 'timeRange'
  | 'time_range'
  | 'toggle'
  | 'boolean'
  | 'switch'
  | 'singleChoice'
  | 'single_choice'
  | 'multipleChoice'
  | 'multiple_choice'
  | 'radio'
  | 'radioGroup'
  | 'radio_group'
  | 'multiChoice'
  | 'multi_choice'
  | 'checkboxes'
  | 'checkboxGroup'
  | 'checkbox_group'
  | 'dropdown'
  | 'select'
  | 'combobox'
  | 'searchableSelect'
  | 'searchable_select'
  | 'chips'
  | 'tags'
  | 'linearScale'
  | 'linear_scale'
  | 'scale'
  | 'slider'
  | 'range'
  | 'rating'
  | 'fileUpload'
  | 'file_upload'
  | 'file'
  | 'singleChoiceGrid'
  | 'single_choice_grid'
  | 'multipleChoiceGrid'
  | 'multiple_choice_grid'
  | 'radioGrid'
  | 'checkboxGrid'
  | 'checkbox_grid'
  | 'multiChoiceGrid'
  | 'multi_choice_grid'
  | 'ratingGrid'
  | 'rating_grid'
  | 'section'
  | 'pageBreak'
  | 'staticContent'
  | 'static_content'
  | 'content'
  | 'description'
  | 'markdown'
  | 'image'
  | 'video'

export type WebUiFormFieldType = WebUiFormInputType

export type WebUiFormOptionPrimitive = string | number | boolean

export interface WebUiFormOptionObject {
  id?: string
  key?: string
  value?: unknown
  label?: string
  title?: string
  name?: string
  description?: string
  hint?: string
  score?: number
  correct?: boolean
  gotoSection?: string
  goToSection?: string
  terminal?: boolean
  [key: string]: unknown
}

export type WebUiFormOption = WebUiFormOptionPrimitive | WebUiFormOptionObject

export interface WebUiFormValidation {
  required?: boolean
  min?: number
  max?: number
  minLength?: number
  min_length?: number
  maxLength?: number
  max_length?: number
  pattern?: string
  format?: 'email' | 'url' | 'phone' | 'number' | 'integer' | 'date' | 'time' | 'date-time' | 'custom' | string
  message?: string
  messages?: Record<string, string>
  [key: string]: unknown
}

export interface WebUiFormBranchRule {
  when?: unknown
  value?: unknown
  gotoSection?: string
  goToSection?: string
  terminal?: boolean
  [key: string]: unknown
}

export interface WebUiFormBranching {
  defaultSection?: string
  default_section?: string
  rules?: readonly WebUiFormBranchRule[]
  [key: string]: unknown
}

export interface WebUiFormQuiz {
  points?: number
  correctAnswer?: unknown
  correctAnswers?: readonly unknown[]
  feedback?: Record<string, unknown>
  [key: string]: unknown
}

export interface WebUiFormField {
  id: string
  type: WebUiFormInputType | string
  label?: string
  title?: string
  question?: string
  description?: string
  helpText?: string
  hint?: string
  placeholder?: string
  content?: string
  text?: string
  body?: string
  markdown?: string
  stateKey?: string
  state_key?: string
  answerKey?: string
  answer_key?: string
  name?: string
  required?: boolean
  disabled?: boolean
  readonly?: boolean
  visibleIf?: string
  visible_if?: string
  default?: unknown
  defaultValue?: unknown
  value?: unknown
  options?: readonly WebUiFormOption[]
  choices?: readonly WebUiFormOption[]
  items?: readonly WebUiFormOption[]
  rows?: readonly WebUiFormOption[]
  columns?: readonly WebUiFormOption[]
  cols?: readonly WebUiFormOption[]
  min?: number
  max?: number
  step?: number
  scaleStart?: number
  scaleEnd?: number
  from?: number
  to?: number
  ratingMax?: number
  minLabel?: string
  maxLabel?: string
  fromLabel?: string
  toLabel?: string
  accept?: string
  multiple?: boolean
  maxFiles?: number
  max_files?: number
  span?: number | 'full'
  columnSpan?: number | 'full'
  column_span?: number | 'full'
  validation?: WebUiFormValidation
  branching?: WebUiFormBranching
  quiz?: WebUiFormQuiz
  [key: string]: unknown
}

export interface WebUiFormInputs {
  fields?: readonly WebUiFormField[]
  questions?: readonly WebUiFormField[]
  submitLabel?: string
  submitPlacement?: 'top' | 'bottom' | 'before' | 'above' | 'after' | 'below' | string
  actionPlacement?: 'top' | 'bottom' | 'before' | 'above' | 'after' | 'below' | string
  autoCommit?: boolean
  commitOnChange?: boolean
  showSubmit?: boolean
  layout?: 'stack' | 'responsiveGrid' | 'responsive-grid' | 'grid' | 'auto-grid'
  fieldLayout?: 'stack' | 'responsiveGrid' | 'responsive-grid' | 'grid' | 'auto-grid'
  minFieldWidth?: number
  min_field_width?: number
  [key: string]: unknown
}

export interface WebUiListFilter {
  key: string
  stateKey?: string
  value?: unknown
  enabledIf?: string
  operator?: 'equals' | 'contains' | 'includes' | 'in' | 'truthy' | 'lt' | 'lte' | 'gt' | 'gte'
}

export interface WebUiListSortOption {
  key: string
  direction?: 'asc' | 'desc'
  numeric?: boolean
}

export interface WebUiListSort {
  key?: string
  direction?: 'asc' | 'desc'
  numeric?: boolean
  stateKey?: string
  value?: unknown
  options?: Readonly<Record<string, WebUiListSortOption>>
}

export interface WebUiListMetaField {
  key: string
  label?: string
  kind?: 'text' | 'badge' | 'boolean'
  trueLabel?: string
  falseLabel?: string
}

export type WebUiActionButtonKind = 'primary' | 'secondary' | 'danger'
export type WebUiActionButtonFill = 'solid' | 'outline' | 'clear'

export interface WebUiActionButton {
  id: string
  label?: string
  title?: string
  description?: string
  icon?: string
  kind?: WebUiActionButtonKind
  fill?: WebUiActionButtonFill
  disabled?: boolean
  enabledIf?: string
  value?: unknown
  stateKey?: string
  selected?: boolean
  connected?: boolean
  node_status?: string
  state?: string
  whenKey?: string
  whenEquals?: unknown
}

export interface WebUiListItemButton extends WebUiActionButton {}

export interface WebUiActionsInputs {
  buttons?: readonly WebUiActionButton[]
  variant?: 'tabs' | 'segmented' | 'toolbar' | 'stack' | 'header'
  size?: 'small' | 'default' | 'medium'
  [key: string]: unknown
}

export interface WebUiListInputs {
  variant?: 'list' | 'cards'
  titleKey?: string
  subtitleKey?: string
  previewKey?: string
  imageKey?: string
  imageAltKey?: string
  badgeKey?: string
  meta?: readonly WebUiListMetaField[]
  groupBy?: string
  groupTitleKey?: string
  groupSubtitleKey?: string
  groupDisplay?: 'sections' | 'accordion'
  filters?: readonly WebUiListFilter[]
  sort?: WebUiListSort
  buttons?: readonly WebUiListItemButton[]
  search?: boolean
  searchEnabled?: boolean
  searchPlaceholder?: string
  addButton?: boolean
  addButtonLabel?: string
  cardMinWidth?: number
  cardImageRatio?: string
  emptyText?: string
  [key: string]: unknown
}

export interface WebUiDetailsInputs {
  selectedStateKey?: string
  stateKey?: string
  fields?: readonly Record<string, unknown>[]
  imageKey?: string
  imageAltKey?: string
  imageRatio?: string
  [key: string]: unknown
}

export interface WebUiLayoutArea {
  id: string
  role?: string
  label?: string
  width?: number
  [key: string]: unknown
}

export interface WebUiLayout {
  type: string
  pattern?: string
  sidebarWidth?: number
  auxWidth?: number
  areas: readonly WebUiLayoutArea[]
  [key: string]: unknown
}

export interface WebUiChatInputs {
  multiline?: boolean
  composerRows?: number
  composerAutoGrow?: boolean
  sendOnEnter?: boolean
  sendOnCtrlEnter?: boolean
  placeholder?: string
  hint?: string
  sendCommand?: string
  openCommand?: string
  [key: string]: unknown
}

export interface WebUiWidgetConfig {
  id: string
  type: WebUiWidgetType | string
  area?: string
  title?: string
  dataSource?: Record<string, unknown>
  inputs?: WebUiFormInputs | WebUiListInputs | WebUiDetailsInputs | WebUiChatInputs | WebUiActionsInputs | Record<string, unknown>
  actions?: readonly WebUiAction[]
  [key: string]: unknown
}

export interface WebUiFormWidgetConfig extends WebUiWidgetConfig {
  type: 'ui.form'
  inputs?: WebUiFormInputs
}

export interface WebUiListWidgetConfig extends WebUiWidgetConfig {
  type: 'ui.list'
  inputs?: WebUiListInputs
}

export interface WebUiDetailsWidgetConfig extends WebUiWidgetConfig {
  type: 'item.details'
  inputs?: WebUiDetailsInputs
}

export interface WebUiChatWidgetConfig extends WebUiWidgetConfig {
  type: 'ui.chat'
  inputs?: WebUiChatInputs
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
