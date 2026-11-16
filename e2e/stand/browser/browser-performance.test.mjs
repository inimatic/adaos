import test from 'node:test'
import assert from 'node:assert/strict'
import {readIdentity,readPerformance,performanceBudgetsForProfile} from './browser-performance.mjs'

test('slow first data cannot pass because rendering eventually succeeds', () => {
  const report=readPerformance([], {initial_data_wait_ms:8000,first_useful_data_ms:9000})
  assert.equal(report.passed,false)
  assert.deepEqual(report.violations,['initial_data_budget_exceeded'])
})
test('missing measurements cannot masquerade as a fast observation', () => {
  assert.equal(readPerformance([],{}).passed,false)
})
test('cache evidence groups identical reads and excludes mutations', () => {
  const report=readPerformance([
    {identity:'one',readOnly:true,duration_ms:800},
    {identity:'one',readOnly:true,duration_ms:600},
    {identity:'two',readOnly:true,duration_ms:300},
    {identity:'write',readOnly:false,duration_ms:9000},
  ],{initial_data_wait_ms:900,first_useful_data_ms:1900})
  assert.equal(report.passed,true)
  assert.equal(report.read_count,3)
  assert.deepEqual(report.repeated_reads,[{identity:'one',count:2}])
  assert.equal(report.slowest_reads[0].duration_ms,800)
})
test('request correlation does not conceal repeated reads, while arguments and scope remain distinct', () => {
  const url='http://127.0.0.1:8777/api/admin/root_mcp/call'
  const body={tool_id:'example.show',arguments:{id:'one'},dry_run:true,request_id:'first'}
  assert.equal(readIdentity(url,'POST',body),readIdentity(url,'POST',{...body,request_id:'second'}))
  assert.notEqual(readIdentity(url,'POST',body),readIdentity(url,'POST',{...body,arguments:{id:'two'}}))
  assert.notEqual(readIdentity(url+'?scope=a','GET'),readIdentity(url+'?scope=b','GET'))
})
test('slow startup cannot be hidden by fast data after the shell finally appears', () => {
  assert.equal(readPerformance([],{initial_data_wait_ms:10,first_useful_data_ms:18000}).passed,false)
})

test('operator profiles preserve the strict default and report the admitted local budget',()=>{
  const timings={initial_data_wait_ms:9390,first_useful_data_ms:19131}
  assert.equal(readPerformance([],timings).passed,false)
  const local=readPerformance([],timings,performanceBudgetsForProfile('shared_local'))
  assert.equal(local.passed,true)
  assert.equal(local.profile,'shared_local')
  assert.deepEqual(local.budgets,{initial_data_wait_ms:15000,first_useful_data_ms:30000})
  assert.equal(readPerformance([],{initial_data_wait_ms:15001,first_useful_data_ms:20000},performanceBudgetsForProfile('shared_local')).passed,false)
  assert.equal(readPerformance([],{},performanceBudgetsForProfile('shared_local')).passed,false)
  assert.throws(()=>performanceBudgetsForProfile('unbounded'),/Unknown operator/)
})
