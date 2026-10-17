// Only deliberately interrupted reads of the owned fixture are expected.
// Attach ownership immediately, while preserving the exact rejection for await.
// This prevents an event timeout from killing Node before its evidence finally.
export function observePending(pending) {
  void pending.catch(() => {})
  return pending
}

export function isExpectedFixtureRestartReadError({ restartWindow, message, method, tool }) {
  if (!restartWindow || !/ECONNREFUSED|ECONNRESET/.test(message)) return false
  if (method === 'GET') return true // This caller already restricted the route to owned attachments.
  return ['query_records', 'run_state', 'comparison', 'prepare_comparison', 'adoption_status']
    .some(name => tool === `natural_retouch_skill:${name}`)
}

// The caller admits only its loopback fixture routes. A reset read may be
// retried once; mutations and unknown submission outcomes must never be replayed.
export async function fetchOwnedReadWithOneRetry(operation, { method, tool }, onRetry) {
  try { return await operation() } catch (error) {
    const message = String(error.message || error).split('\n')[0]
    const read = method === 'GET' || method === 'POST' &&
      ['query_records', 'run_state', 'comparison', 'adoption_status', 'read_attachment']
        .some(name => tool === `natural_retouch_skill:${name}`)
    if (!read || !/\bECONNRESET\b/.test(message)) throw error
    onRetry?.({ method, tool, message })
    return operation()
  }
}

// Client discovery probes alternate local runtime slots before choosing one.
// They do not authorize application reads or mutations on another authority.
export function isOptionalLocalDiscoveryRequest({url, method}) {
  if (method !== 'GET') return false
  let target
  try { target = new URL(url) } catch { return false }
  if (target.protocol !== 'http:' || target.username || target.password
    || !['127.0.0.1', 'localhost'].includes(target.hostname)
    || !['8777', '8778'].includes(target.port)) return false
  if (target.pathname === '/api/ping') return target.search === ''
  return target.pathname === '/api/node/status' && target.search === '?profile=probe'
}
