// Only deliberately interrupted reads of the owned fixture are expected.
export function isExpectedFixtureRestartReadError({ restartWindow, message, method, tool }) {
  if (!restartWindow || !/ECONNREFUSED|ECONNRESET/.test(message)) return false
  if (method === 'GET') return true // This caller already restricted the route to owned attachments.
  return ['query_records', 'run_state', 'comparison', 'prepare_comparison', 'adoption_status']
    .some(name => tool === `natural_retouch_skill:${name}`)
}
