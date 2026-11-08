import crypto from 'node:crypto'

// Trusted browser observations. No response bodies or request arguments enter
// this report, and candidates cannot change the acceptance budget.
export function readIdentity(url, method, body) {
  const target=new URL(url)
  const stable=value => Array.isArray(value) ? value.map(stable) : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key,stable(value[key])])) : value
  const semanticBody=body?.tool_id || body?.tool || body?.target ? {
    tool:body.tool_id||body.tool||body.target, arguments:body.arguments||body.params||{},
    dry_run:body.dry_run, webspace:body.context?.webspace_id,
  } : body
  return crypto.createHash('sha256').update(JSON.stringify(stable([
    target.origin,target.pathname,method,[...target.searchParams].sort(),semanticBody||null,
  ]))).digest('hex')
}

export function readPerformance(requests, timings, {initialDataBudgetMs = 5000, firstUsefulBudgetMs = 15000} = {}) {
  const reads = requests.filter(row => row.readOnly === true)
  const counts = new Map()
  for (const row of reads) counts.set(row.identity, (counts.get(row.identity) || 0) + 1)
  const initial = timings.initial_data_wait_ms
  const violations = []
  if (!Number.isFinite(initial) || initial < 0) violations.push('initial_data_observation_missing')
  else if (initial > initialDataBudgetMs) violations.push('initial_data_budget_exceeded')
  const useful=timings.first_useful_data_ms
  if (!Number.isFinite(useful) || useful < 0) violations.push('first_useful_data_observation_missing')
  else if (useful > firstUsefulBudgetMs) violations.push('first_useful_data_budget_exceeded')
  return {
    schema: 'adaos.builder.browser_performance.v1',
    timings, budgets: {initial_data_wait_ms: initialDataBudgetMs,first_useful_data_ms:firstUsefulBudgetMs},
    read_count: reads.length, distinct_reads: counts.size,
    repeated_reads: [...counts].filter(([,count]) => count > 1).map(([identity,count]) => ({identity,count})),
    slowest_reads: reads.filter(row => Number.isFinite(row.duration_ms))
      .sort((a,b) => b.duration_ms-a.duration_ms).slice(0,12),
    passed: violations.length === 0, violations,
    cache_scope: 'Same browser session; repeated reads may be justified by authoritative invalidation. Counts alone do not prove a cache defect.',
  }
}
