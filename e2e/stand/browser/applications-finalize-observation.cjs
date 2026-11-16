// Exercise Applications' authored state machine with the Client expression runtime.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const root = path.resolve(__dirname, '../../..')
const client = path.join(root, 'src/adaos/integrations/adaos-client')
const ts = require(path.join(client, 'node_modules/typescript'))
const source = fs.readFileSync(path.join(client, 'src/app/runtime/state-expression.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const runtime = { exports: {} }
new Function('module', 'exports', 'require', compiled)(runtime, runtime.exports, require)
const evaluate = runtime.exports.evaluateStateExpression
const schema = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const app = schema.ui.application
const page = app.desktop.pageSchema
const widgets = Object.fromEntries(page.widgets.map(w => [w.id, w]))
const observe = page.autoActions.find(a => a.id === 'finalize-operation-observation')
const accept = observe.action.followups[0]
assert.equal(observe.action.type, 'callMcp')
assert.equal(observe.action.dryRun, true)
assert.equal(observe.action.target, 'applications.get_operation')
assert.equal(observe.action.params.operation_id, '$state.finalizeOperation.operation.operation_id')
const operation = { operation_id: 'accept-1', application_id: 'retouch', status: 'failed',
  plan: { action: 'promote_stable', candidate_id: 'candidate-1', release_digest: 'release-1' },
  task: { id: 'accept-1', status: 'failed', error: 'Dependency conflict before data adoption.' } }
const initial = { selectedApplicationId: 'retouch', detailApplicationId: 'retouch', dashboardSection: 'dashboard',
  finalizeSubmitted: true, finalizeContext: { application_id: 'retouch', candidate_id: 'candidate-1', release_digest: 'release-1' },
  finalizeOperation: { operation: { operation_id: 'accept-1' } }, finalizeError: {}, finalizeObserved: {},
  dashboardRuntimeReleaseDigest: 'release-1', dashboardOperationTask: {}, dashboardOperationId: '',
  dashboardLatestOperation: { action: 'renew_beta', status: 'succeeded' } }
assert.equal(evaluate(widgets['finalize-submission-progress'].visibleIf, initial), true)
assert.equal(evaluate(observe.enabledIf, initial), true)
const observed = { ...initial, finalizeObserved: { operation }, finalizeObservation: { operation } }
assert.equal(evaluate(accept.enabledIf, observed), true)
assert.equal(evaluate(widgets['finalize-submission-progress'].visibleIf, observed), false)
assert.equal(evaluate(widgets['finalize-observed-progress'].visibleIf, observed), true)
assert.equal(evaluate(observe.enabledIf, observed), false)
const renewal = widgets['dashboard-positions'].inputs.buttons.find(b => b.id === 'renew_beta')
assert.equal(evaluate(renewal.enabledIf, initial), false)
assert.equal(evaluate(renewal.enabledIf, observed), true)
const blocked = { ...observed, finalizeObserved: { operation: { ...operation, status: 'unknown' } } }
assert.equal(evaluate(observe.enabledIf, blocked), false)
assert.equal(evaluate(renewal.enabledIf, blocked), true)
const expiry = widgets['dashboard-positions'].inputs.meta.find(m => m.key === 'expires_at')
assert.equal(expiry.kind, 'date')
const modal = app.modals['finalize-application'].schema.widgets.find(w => w.id === 'finalize-progress')
assert.equal(evaluate(modal.visibleIf, observed), true)
assert.equal(modal.dataSource.value, '$state.finalizeObserved.operation.task')
for (const stale of [
  { ...operation, operation_id: 'another-attempt' },
  { ...operation, application_id: 'another-app' },
  { ...operation, plan: { ...operation.plan, candidate_id: 'another-candidate' } },
  { ...operation, plan: { ...operation.plan, release_digest: 'another-release' } },
]) assert.equal(evaluate(accept.enabledIf, { ...initial, finalizeObservation: { operation: stale } }), false)
const completed = { ...observed, finalizeObserved: { operation: { ...operation, status: 'succeeded', task: { id: 'accept-1', status: 'completed' } } } }
assert.equal(evaluate(widgets['finalize-submission-progress'].visibleIf, completed), false)
assert.equal(evaluate(widgets['finalize-observed-progress'].visibleIf, completed), false)
assert.equal(evaluate(observe.enabledIf, completed), false)
assert.equal(evaluate(widgets['finalize-observed-progress'].visibleIf, { ...observed, selectedApplicationId: 'another-app' }), false)
console.log(JSON.stringify({ passed: true, latestOperation: 'renew_beta', retainedFinalize: 'failed' }))
