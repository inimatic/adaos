import test from 'node:test'
import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {isExpectedFixtureRestartReadError as expected, isOptionalLocalDiscoveryRequest as discovery, fetchOwnedReadWithOneRetry as fetchRead} from './controlled-faults.mjs'

test('a reset owned read recovers once and retains sanitized retry evidence', async () => {
  for (const context of [{method:'GET'}, {method:'POST',tool:'natural_retouch_skill:run_state'}]) {
    let calls=0; const evidence=[]
    const result=await fetchRead(async()=>{
      if (++calls===1) throw new Error('route.fetch: read ECONNRESET\nprivate transport headers')
      return 'owned response'
    },context,row=>evidence.push(row))
    assert.equal(result,'owned response');assert.equal(calls,2)
    assert.deepEqual(evidence,[{...context,tool:context.tool,message:'route.fetch: read ECONNRESET'}])
  }
})

test('owned read recovery never repeats mutations or persistent faults', async () => {
  for (const tool of ['run_command','save_mask','adopt_result','upload_attachment','prepare_comparison']) {
    let calls=0
    await assert.rejects(()=>fetchRead(async()=>{calls++;throw new Error('ECONNRESET')},
      {method:'POST',tool:`natural_retouch_skill:${tool}`}),/ECONNRESET/)
    assert.equal(calls,1)
  }
  for (const message of ['ECONNRESET','Timeout','HTTP 500']) {
    let calls=0
    await assert.rejects(()=>fetchRead(async()=>{calls++;throw new Error(message)}, {method:'GET'}),new RegExp(message))
    assert.equal(calls,message==='ECONNRESET'?2:1)
  }
})

test('a refused or reset owned read is expected only during the declared restart', () => {
  const base = {restartWindow: true, method: 'POST', tool: 'natural_retouch_skill:query_records'}
  for (const message of ['route.fetch: connect ECONNREFUSED', 'route.fetch: read ECONNRESET']) {
    assert.equal(expected({...base, message}), true)
    assert.equal(expected({...base, message, restartWindow: false}), false)
    assert.equal(expected({...base, message, tool: 'natural_retouch_skill:run_command'}), false)
    assert.equal(expected({...base, message, tool: 'natural_retouch_skill:save_mask'}), false)
    assert.equal(expected({...base, message, tool: 'other_skill:query_records'}), false)
  }
  assert.equal(expected({...base, message: 'route.fetch: Timeout'}), false)
})

test('only bounded read-only loopback discovery may use an alternate authority', () => {
  for (const host of ['localhost', '127.0.0.1']) for (const port of [8777, 8778]) {
    for (const path of ['/api/ping', '/api/node/status?profile=probe']) {
      const url = `http://${host}:${port}${path}`
      assert.equal(discovery({url, method: 'GET'}), true)
      for (const method of ['POST', 'PUT', 'DELETE']) assert.equal(discovery({url, method}), false)
    }
  }
  for (const url of ['http://127.0.0.1:9000/api/ping', 'https://localhost:8777/api/ping',
    'http://remote.example:8777/api/ping', 'http://localhost.evil:8777/api/ping',
    'http://localhost:8777/api/tools/call', 'http://localhost:8778/api/photos',
    'http://localhost:8777/api/node/status', 'http://localhost:8777/api/node/status?profile=full',
    'http://localhost:8777/api/node/status?profile=probe&extra=1',
    'http://localhost:8777/api/ping?token=secret', 'http://user@localhost:8777/api/ping', 'invalid']) {
    assert.equal(discovery({url, method: 'GET'}), false, url)
  }
})

test('a timed-out event remains observable after another action, without killing evidence capture',()=>{
  const module=JSON.stringify(new URL('./controlled-faults.mjs',import.meta.url).href)
  const guarded=spawnSync(process.execPath,['--unhandled-rejections=strict','--input-type=module','-e',`
    import {observePending} from ${module};
    const event=observePending(Promise.reject(new Error('event timeout')));
    await new Promise(resolve=>setTimeout(resolve,5));
    let observed=false;try{await event}catch(error){observed=error.message==='event timeout'}
    if(!observed)process.exit(2);
  `],{encoding:'utf8'})
  assert.equal(guarded.status,0,guarded.stderr)
  const unowned=spawnSync(process.execPath,['--unhandled-rejections=strict','--input-type=module','-e',`
    const event=Promise.reject(new Error('event timeout'));
    await new Promise(resolve=>setTimeout(resolve,5));
    try{await event}catch{}
  `],{encoding:'utf8'})
  assert.notEqual(unowned.status,0)
  assert.match(unowned.stderr,/event timeout/)
})
