import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
import { chromium } from './node_modules/playwright/index.mjs'

const api=process.env.ADAOS_E2E_API || 'http://127.0.0.1:8777'
const client=process.env.ADAOS_E2E_CLIENT || 'http://127.0.0.1:8100'
const token=process.env.ADAOS_E2E_HUB_TOKEN, webspace=process.env.ADAOS_E2E_WEBSPACE
const subnet=process.env.ADAOS_E2E_SUBNET_ID
assert.ok(token && webspace?.startsWith('desktop-codex-'), 'isolated Webspace and explicit token required')
const cases=JSON.parse(process.env.ADAOS_E2E_PROJECTION_CASES)
const transitions=Number(process.env.ADAOS_E2E_TRANSITIONS || 50)
const idleSeconds=Number(process.env.ADAOS_E2E_IDLE_SECONDS || 900)
const output=path.resolve(process.env.ADAOS_E2E_ARTIFACT_DIR || 'e2e/artifacts/runtime-projection-convergence')
await fs.mkdir(output,{recursive:true})
const report={startedAt:new Date().toISOString(),webspace,cases,transitions:[],errors:[],readFailures:[],sockets:[],hubLatencyMs:[],snapshotRequests:0,repairRequests:0,httpNavigationRequests:0,httpNavigationCalls:[]}
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({viewport:{width:1440,height:960},serviceWorkers:'block'})
await context.addInitScript(({api,token,webspace,subnet})=>{
 window.__ADAOS_BASE__=api;window.__ADAOS_TOKEN__=token
 for(const [key,value] of Object.entries({adaos_device_id:'e2e-projection-'+webspace,adaos_webspace_id:webspace,
  adaos_hub_base:api,adaos_local_hub_base:api,adaos_try_local_hub:'1',adaos_hub_token:token,
  adaos_local_subnet_id:subnet,adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'en'}))localStorage.setItem(key,value)
 const samples=window.__projectionSamples=[]
 const availability=window.__availabilitySamples=[]
 function sample(){
  const app=window.ng?.getComponent(document.querySelector('app-root'))
  if(app?.availabilitySummary$&&!window.__availabilitySubscribed){
   window.__availabilitySubscribed=true
   app.availabilitySummary$.subscribe(value=>{
    const runtime=app.ydoc.getSyncRuntimeSnapshot()
    const summary={at:Date.now(),state:value.state,reason:value.reason,flags:value.flags,
     providerSynced:runtime.providerSynced,projectionReady:runtime.materialization?.ready}
    window.__latestAvailability=summary
    availability.push(summary)
    if(availability.length>1000)availability.shift()
   })
  }
  const desktop=window.ng?.getComponent(document.querySelector('ada-desktop'))
  const live=app?.ydoc?.getMaterializationSnapshot?.()
  const rendered=desktop?.pageSchema?._adaos?.component?.id
  if(rendered){
   const expected=window.__expectedProjectionTransition
   const value={at:performance.now(),rendered,scenario:live?.currentScenario,generation:live?.projectionGeneration,
    ready:live?.ready,urlScenario:new URL(location.href).searchParams.get('scenario_id'),pending:app?.navigationLocation?.hasPendingTransaction?.(),
    unexpectedTarget:Boolean(expected && ![expected.from,expected.to].includes(rendered))}
   const last=samples.at(-1)
   if(!last || ['rendered','scenario','generation','ready','urlScenario','pending','unexpectedTarget'].some(key=>last[key]!==value[key]))samples.push(value)
   if(samples.length>2000)samples.shift()
  }
  requestAnimationFrame(sample)
 }
 requestAnimationFrame(sample)
},{api,token,webspace,subnet})
const page=await context.newPage()
page.on('pageerror',error=>report.errors.push(String(error.message).slice(0,500)))
page.on('request',request=>{
 const url=new URL(request.url())
 if(url.pathname.endsWith('/materialization/snapshot'))report.snapshotRequests++
 if(url.pathname.endsWith('/materialization/repair'))report.repairRequests++
 if(url.pathname.endsWith('/scenario') && request.method()==='POST'){
  report.httpNavigationRequests++
  const body=request.postDataJSON() || {}
  report.httpNavigationCalls.push({at:Date.now(),path:url.pathname,scenario:body.scenario_id,navigationId:body.navigation_id})
 }
})
page.on('response',async response=>{
 if(response.status()<400 || !new URL(response.url()).pathname.endsWith('/tools/call'))return
 try{
  const request=response.request().postDataJSON()
  const result=await response.json().catch(()=>({}))
  const detail=result.detail || result
  const generation=request?.context?.projection_generation || null
  const current=await page.evaluate(()=>window.ng?.getComponent(document.querySelector('app-root'))?.ydoc?.getMaterializationSnapshot?.().projectionGeneration).catch(()=>null)
  report.readFailures.push({tool:request?.tool,status:response.status(),code:detail.code || detail.error || 'unknown',generation,currentGeneration:current,
   applicationId:request?.context?.application_id,releaseDigest:request?.context?.application_release_digest,scenario:request?.context?.scenario_id,
   contextWebspace:request?.context?.webspace_id})
 }catch{}
})
page.on('websocket',ws=>{
 if(!new URL(ws.url()).pathname.startsWith('/yws/'))return
 const socket={opened:Date.now(),received:0,bytes:0,largeFrames:0,largeStep2:0};report.sockets.push(socket)
 ws.on('framereceived',frame=>{const size=Buffer.byteLength(frame.payload);socket.received++;socket.bytes+=size;if(size>256*1024){socket.largeFrames++;const bytes=Buffer.from(frame.payload);if(bytes[0]===0&&bytes[1]===1)socket.largeStep2++}})
 ws.on('close',()=>socket.closed=Date.now())
})
async function read(){return page.evaluate(()=>{
 const app=window.ng?.getComponent(document.querySelector('app-root'))
 const desktop=window.ng?.getComponent(document.querySelector('ada-desktop'))
 const runtime=app?.ydoc?.getSyncRuntimeSnapshot?.()
 return {rendered:desktop?.pageSchema?._adaos?.component?.id,materialization:runtime?.materialization,
  convergence:runtime?.projectionConvergence,connection:runtime?.connectionState,url:location.search,
  widgetCount:desktop?.pageSchema?.widgets?.length || 0,availability:window.__latestAvailability,
  preObserverRevisionHook:Boolean(app?.ydoc?.doc?._observers?.get('beforeObserverCalls')?.size)}
})}
async function waitFor(scenario){
 await page.waitForFunction(scenario=>{
  const app=window.ng?.getComponent(document.querySelector('app-root'))
  const runtime=app?.ydoc?.getSyncRuntimeSnapshot?.()
  const rendered=window.ng?.getComponent(document.querySelector('ada-desktop'))?.pageSchema?._adaos?.component?.id
  return runtime?.connectionState==='connected' && runtime?.providerSynced && runtime?.materialization?.ready && runtime.materialization.currentScenario===scenario &&
   rendered===scenario && runtime.projectionConvergence?.state==='applied' &&
   new URL(location.href).searchParams.get('scenario_id')===scenario
 },scenario,{timeout:60000})
}
async function switchTo(scenario,index){
 const started=Date.now()
 await page.evaluate(scenario=>{
  const app=window.ng.getComponent(document.querySelector('app-root'))
  window.__expectedProjectionTransition={from:app.ydoc.getMaterializationSnapshot().currentScenario,to:scenario}
 },scenario)
 const phases=await page.evaluate(async({webspace,scenario})=>{
  const app=window.ng.getComponent(document.querySelector('app-root'))
  const desktop=window.ng.getComponent(document.querySelector('ada-desktop'))
  if(desktop?.pageActions){
   const ok=await desktop.pageActions.handle({type:'callHost',target:'desktop.scenario.set',
    params:{webspace_id:webspace,scenario_id:scenario,wait_for_rebuild:false}})
   if(!ok)throw new Error('production_page_action_rejected:'+scenario)
   return {source:'PageActionService',navigationId:app.ydoc.getMaterializationSnapshot().navigationId}
  }
  throw new Error('production_page_action_service_missing')
 },{webspace,scenario})
 await waitFor(scenario)
 await page.evaluate(()=>{window.__expectedProjectionTransition=null})
 assert.ok((await read()).widgetCount>0,'applied application has no page widgets')
 report.transitions.push({index,scenario,durationMs:Date.now()-started,generation:(await read()).materialization.projectionGeneration,phases})
 console.log(JSON.stringify({transition:index,scenario,durationMs:report.transitions.at(-1).durationMs}))
}
async function checkDeclaredStateUrl(){
 const proof=await page.evaluate(()=>{
  const desktop=window.ng.getComponent(document.querySelector('ada-desktop'))
  const binding=desktop.browserNavigationBindings().find(b=>/search|filter|settings/i.test(b.queryKey) && b.history!=='ephemeral')
  if(!binding)return null
  const previous=desktop.pageState.get(binding.stateKey)
  desktop.pageState.set(binding.stateKey,'codex-qualification')
  return {stateKey:binding.stateKey,queryKey:binding.queryKey,previous}
 })
 if(!proof)return
 await page.waitForFunction(key=>new URL(location.href).searchParams.get(key)==='codex-qualification',proof.queryKey)
 report.declaredStateUrl={stateKey:proof.stateKey,queryKey:proof.queryKey,passed:true}
 await page.evaluate(proof=>window.ng.getComponent(document.querySelector('ada-desktop')).pageState.set(proof.stateKey,proof.previous),proof)
}
async function checkPendingStateUrl(){
 const proof=await page.evaluate(()=>{
  const desktop=window.ng.getComponent(document.querySelector('ada-desktop'))
  const app=window.ng.getComponent(document.querySelector('app-root'))
  const binding=desktop.browserNavigationBindings().find(b=>/search|filter|settings/i.test(b.queryKey) && b.history!=='ephemeral')
  if(!binding)throw new Error('declared pending-state URL binding missing')
  const previous=desktop.pageState.get(binding.stateKey)
  const before=location.href
  const transaction=app.navigationLocation.beginTransaction({scenario_id:'web_desktop'},'replace')
  desktop.pageState.set(binding.stateKey,'codex-pending-state')
  const unchanged=location.href===before
  transaction.commit()
  const applied=new URL(location.href).searchParams.get(binding.queryKey)==='codex-pending-state'
  desktop.pageState.set(binding.stateKey,previous)
  return {unchanged,applied,stateKey:binding.stateKey,queryKey:binding.queryKey}
 })
 assert.equal(proof.unchanged,true,'pending PageState prematurely rewrote the address bar')
 assert.equal(proof.applied,true,'destination PageState was lost at transaction commit')
 report.pendingStateUrl=proof
}
async function validateProjectionSamples(){
 const samples=await page.evaluate(()=>window.__projectionSamples)
 const allowed=new Set(cases.map(c=>c.scenario_id))
 for(const sample of samples){
  assert.ok(allowed.has(sample.rendered),'unexpected application frame')
  assert.equal(sample.unexpectedTarget,false,'third application appeared during navigation')
  assert.equal(sample.rendered,sample.scenario,'rendered an obsolete scenario')
  if(!sample.pending)assert.equal(sample.rendered,sample.urlScenario,'URL differs from applied scenario')
 }
 return samples
}
let probing=false
const pingTimer=setInterval(async()=>{
 if(probing)return
 probing=true
 const started=Date.now()
 try{
  const response=await fetch(api+'/api/ping',{headers:{'X-AdaOS-Token':token},signal:AbortSignal.timeout(10000)})
  if(response.ok)report.hubLatencyMs.push(Date.now()-started)
 }catch{report.hubLatencyMs.push(10000)}finally{probing=false}
},1000)
try{
 const start=Date.now()
 await page.goto(`${client}/?zone=lo&webspace_id=${webspace}&scenario_id=web_desktop&view=home`,{waitUntil:'domcontentloaded',timeout:90000})
 await waitFor('web_desktop')
 report.coldStartMs=Date.now()-start
 for(let i=0;i<transitions;i++){
  const scenario=cases[(i+1)%cases.length].scenario_id
  await switchTo(scenario,i)
  if(scenario==='web_desktop'&&!report.declaredStateUrl)await checkDeclaredStateUrl()
 }
 // Browser history uses the production resolver, with no replayed business action.
 if(transitions>1){
  const previous=report.transitions.at(-2).scenario
  await page.goBack();await waitFor(previous)
  await page.goForward();await waitFor(report.transitions.at(-1).scenario)
  report.history='passed'
 }
 // A new publication of the same Application must retire reads, not widgets.
 const sameScenario=report.transitions.at(-1)?.scenario || 'web_desktop'
 const previousGeneration=(await read()).materialization.projectionGeneration
 await switchTo(sameScenario,'same-scenario-noop')
 assert.equal((await read()).materialization.projectionGeneration,previousGeneration,'already-current navigation unexpectedly rebuilt the page')
 const reloadAccepted=await page.evaluate(async webspace=>
  window.ng.getComponent(document.querySelector('ada-desktop')).pageActions.handle({type:'callHost',target:'desktop.webspace.reload',
   params:{webspace_id:webspace,recreate_room:false}}),webspace)
 assert.equal(reloadAccepted,true,'same-page reload was not accepted')
 await page.waitForFunction(previous=>{
  const live=window.ng.getComponent(document.querySelector('app-root')).ydoc.getMaterializationSnapshot()
  return live.ready && live.projectionGeneration!==previous
 },previousGeneration,{timeout:60000})
 await waitFor(sameScenario)
 report.sameScenarioRefresh={scenario:sameScenario,passed:true}
 const rapid=await page.evaluate(async webspace=>{
  const app=window.ng.getComponent(document.querySelector('app-root'))
  const actions=window.ng.getComponent(document.querySelector('ada-desktop')).pageActions
  const navigate=scenario=>actions.handle({type:'callHost',target:'desktop.scenario.set',
    params:{webspace_id:webspace,scenario_id:scenario,wait_for_rebuild:false}})
  const previous=navigate('applications')
  await new Promise(resolve=>setTimeout(resolve,20))
  window.__expectedProjectionTransition={from:app.ydoc.getMaterializationSnapshot().currentScenario,to:'web_desktop'}
  const latest=navigate('web_desktop')
  return {oldResult:await previous,latestResult:await latest,target:'web_desktop'}
 },webspace)
 assert.equal(rapid.latestResult,true,'latest rapid navigation was rejected')
 await waitFor(rapid.target)
 await page.evaluate(()=>{window.__expectedProjectionTransition=null})
 report.rapidNavigation=rapid
 await checkPendingStateUrl()
 // Explicit provider disconnect/reconnect exercises production recovery without
 // restarting Core, touching another tab, or replaying a business operation.
 const recoveryStarted=Date.now()
 await page.evaluate(async()=>{
  const ydoc=window.ng.getComponent(document.querySelector('app-root')).ydoc
  const provider=ydoc.provider
  provider.disconnect()
  await new Promise(resolve=>setTimeout(resolve,300))
  provider.connect()
 })
 await waitFor(report.rapidNavigation.target)
 await page.waitForFunction(()=>window.ng.getComponent(document.querySelector('app-root')).ydoc.getSyncRuntimeSnapshot().connectionState==='connected')
 report.injectedDisconnect={durationMs:Date.now()-recoveryStarted,state:'recovered'}
 report.beforeIdle={at:Date.now(),sockets:report.sockets.length,bytes:report.sockets.reduce((n,s)=>n+s.bytes,0),largeStep2:report.sockets.reduce((n,s)=>n+s.largeStep2,0),repairs:report.repairRequests}
 report.samples=await validateProjectionSamples()
 await fs.writeFile(path.join(output,'checkpoint.json'),JSON.stringify(report,null,2))
 const idleUntil=Date.now()+idleSeconds*1000
 while(Date.now()<idleUntil){
  await page.waitForTimeout(Math.min(30000,idleUntil-Date.now()))
  const state=await read()
  if(Date.now()>report.beforeIdle.at+30000){
   assert.equal(state.availability?.flags?.canTrustStateSync,true,'fresh idle projection lost state-sync trust')
   assert.ok(['ready','relay'].includes(state.availability.state),'idle local availability is not ready/relay')
  }
  console.log(JSON.stringify({idleRemainingSeconds:Math.max(0,Math.round((idleUntil-Date.now())/1000)),sockets:report.sockets.length,repairs:report.repairRequests,
   availability:state.availability?.state,canTrustStateSync:state.availability?.flags?.canTrustStateSync}))
 }
 report.afterIdle=await read()
 report.availabilitySamples=await page.evaluate(()=>window.__availabilitySamples)
 assert.equal(report.afterIdle.availability?.flags?.canTrustStateSync,true,'fresh idle projection lost state-sync trust')
 for(const sample of report.availabilitySamples){
  if(sample.at<report.beforeIdle.at+30000 || !sample.providerSynced || !sample.projectionReady)continue
  assert.equal(sample.flags.canTrustStateSync,true,'idle availability contradicted the fresh live projection')
 }
 assert.equal(report.snapshotRequests,0,'interactive runtime fetched an HTTP snapshot')
 assert.equal(report.repairRequests,0,'normal navigation required projection repair')
 assert.equal(report.httpNavigationRequests,0,'normal/superseded navigation replayed through HTTP fallback')
 assert.equal(report.sockets.length,report.beforeIdle.sockets,'YWS reconnected while idle')
 assert.equal(report.sockets.reduce((n,s)=>n+s.largeStep2,0),report.beforeIdle.largeStep2,'idle replayed a full projection')
 report.samples=await validateProjectionSamples()
 assert.equal(report.readFailures.filter(f=>f.code==='tool_intent_mismatch'&&f.generation&&f.generation===f.currentGeneration).length,0,
  'current widgets used an obsolete tool/SDK authority')
 report.final=await read()
 const durations=report.transitions.map(x=>x.durationMs).sort((a,b)=>a-b)
 report.performance={p50Ms:durations[Math.floor(durations.length*.5)],p95Ms:durations[Math.min(durations.length-1,Math.floor(durations.length*.95))],maxMs:durations.at(-1)}
 const pings=report.hubLatencyMs.slice().sort((a,b)=>a-b)
 report.performance.hubPingP95Ms=pings[Math.min(pings.length-1,Math.floor(pings.length*.95))]
 report.performance.hubPingMaxMs=pings.at(-1)
 assert.ok(report.performance.maxMs<30000,'a normal projection exceeded the interactive convergence budget')
 assert.ok(report.performance.hubPingP95Ms<1000,'owner responsiveness P95 exceeded one second')
 await page.screenshot({path:path.join(output,'accepted.png')})
}catch(error){
 report.errors.push(String(error.stack || error).slice(0,3000))
 report.failure=await read().catch(()=>null)
 report.samples=await page.evaluate(()=>window.__projectionSamples).catch(()=>[])
 report.diagnostics=await page.evaluate(()=>JSON.parse(localStorage.getItem('adaos.runtime_debug.logs.v1') || '[]')).catch(()=>[])
 report.availabilitySamples=await page.evaluate(()=>window.__availabilitySamples).catch(()=>[])
 await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{})
}finally{
 clearInterval(pingTimer)
 report.finishedAt=new Date().toISOString()
 await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2))
 console.log(JSON.stringify({artifact:path.join(output,'report.json'),errors:report.errors,performance:report.performance,snapshotRequests:report.snapshotRequests,repairs:report.repairRequests}))
 await browser.close()
}
if(report.errors.length)process.exitCode=1
