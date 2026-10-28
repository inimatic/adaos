import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
import {chromium} from './node_modules/playwright/index.mjs'

const api=process.env.ADAOS_E2E_API || 'http://127.0.0.1:8777'
const client=process.env.ADAOS_E2E_CLIENT || 'http://127.0.0.1:8100'
const token=process.env.ADAOS_E2E_HUB_TOKEN,webspace=process.env.ADAOS_E2E_WEBSPACE
const subnet=process.env.ADAOS_E2E_SUBNET_ID
const cases=JSON.parse(process.env.ADAOS_E2E_PROJECTION_CASES)
assert.ok(token && webspace?.startsWith('desktop-codex-'))
const output=path.resolve(process.env.ADAOS_E2E_ARTIFACT_DIR)
await fs.mkdir(output,{recursive:true})
const report={startedAt:new Date().toISOString(),steps:[],failedReads:[],errors:[],navigationRejections:[],snapshotRequests:0,repairRequests:0}
report.runtimeSelections=JSON.parse(process.env.ADAOS_E2E_RUNTIME_SELECTIONS || '[]')
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({viewport:{width:1440,height:960},serviceWorkers:'block'})
await context.addInitScript(({api,token,webspace,subnet})=>{
 window.__ADAOS_BASE__=api;window.__ADAOS_TOKEN__=token
 for(const [key,value] of Object.entries({adaos_device_id:'e2e-home-builder-'+webspace,adaos_webspace_id:webspace,
  adaos_hub_base:api,adaos_local_hub_base:api,adaos_try_local_hub:'1',adaos_hub_token:token,
  adaos_local_subnet_id:subnet,adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'en'}))localStorage.setItem(key,value)
},{api,token,webspace,subnet})
const page=await context.newPage()
page.on('pageerror',error=>report.errors.push(String(error.message).slice(0,500)))
page.on('request',request=>{
 const pathname=new URL(request.url()).pathname
 if(pathname.endsWith('/materialization/snapshot'))report.snapshotRequests++
 if(pathname.endsWith('/materialization/repair'))report.repairRequests++
})
page.on('response',async response=>{
 if(response.status()<400 || !new URL(response.url()).pathname.endsWith('/tools/call'))return
 const body=response.request().postDataJSON() || {},result=await response.json().catch(()=>({}))
 const detail=result.detail || result
 report.failedReads.push({tool:body.tool,intent:body.context?.intent,status:response.status(),
  code:detail.code || detail.error,scenario:body.context?.scenario_id,generation:body.context?.projection_generation,
  applicationId:body.context?.application_id,release:body.context?.application_release_digest})
})
async function inspect(){return page.evaluate(()=>{
 const app=window.ng?.getComponent(document.querySelector('app-root'))
 const desktop=window.ng?.getComponent(document.querySelector('ada-desktop'))
 const live=app?.ydoc?.getMaterializationSnapshot?.()
 const data=desktop?.pageData
 const statuses=[...(data?.runtimeDataSourceStatuses || new Map()).values()]
 return {urlScenario:new URL(location.href).searchParams.get('scenario_id'),
  rendered:desktop?.pageSchema?._adaos?.component?.id,selected:live?.currentScenario,
  projection:live?.projectionScenario,ready:live?.ready,missing:live?.missingBranches,
  generation:live?.projectionGeneration,pending:app?.navigationLocation?.hasPendingTransaction?.(),
  connection:app?.ydoc?.getSyncRuntimeSnapshot?.().connectionState,
  statuses:statuses.map(item=>({state:item.value?.state,target:item.value?.target,
   code:item.value?.error?.code,status:item.value?.error?.status,reason:item.value?.reason})),
  notifications:(app?.notifications?.getSnapshot?.() || []).slice(-6).map(item=>({source:item.source,code:item.code,message:String(item.message || '').slice(0,500)}))}
})}
async function ready(scenario){
 const expected=cases.find(item=>item.scenario_id===scenario)
 assert.ok(expected,'expected selected Application identity is required')
 await page.waitForFunction(({scenario,expected})=>{
  const app=window.ng?.getComponent(document.querySelector('app-root'))
  const desktop=window.ng?.getComponent(document.querySelector('ada-desktop'))
  const live=app?.ydoc?.getMaterializationSnapshot?.()
  return live?.ready && live.applicationId===expected.application_id &&
   live.applicationReleaseDigest===expected.release_digest &&
   desktop?.pageSchema?._adaos?.component?.id===scenario &&
   new URL(location.href).searchParams.get('scenario_id')===scenario &&
   !app.navigationLocation.hasPendingTransaction()
 },{scenario,expected},{timeout:60000})
}
async function scenario(id){
 const started=Date.now()
 const accepted=await page.evaluate(async({webspace,id})=>{
  const desktop=window.ng.getComponent(document.querySelector('ada-desktop'))
  return desktop.pageActions.handle({type:'callHost',target:'desktop.scenario.set',
   params:{webspace_id:webspace,scenario_id:id,wait_for_rebuild:false}})
 },{webspace,id})
 if(!accepted)report.navigationRejections.push({scenario:id,state:await inspect()})
 await ready(id)
 report.steps.push({action:'scenario',scenario:id,durationMs:Date.now()-started,state:await inspect()})
 console.log(JSON.stringify({phase:'scenario',id,accepted,durationMs:Date.now()-started}))
}
async function picker(){
 const started=Date.now()
 assert.equal(await page.evaluate(()=>window.ng.getComponent(document.querySelector('ada-desktop')).pageActions.handle(
  {type:'openModal',params:{modalId:'project-picker'}})),true)
 const table=page.locator('[data-webui-widget-id$="project-picker-table"]')
 await table.locator('tr[data-record-id]').first().waitFor({state:'visible',timeout:30000})
 report.steps.push({action:'project-picker',durationMs:Date.now()-started,rows:await table.locator('tr[data-record-id]').count()})
 console.log(JSON.stringify({phase:'picker',durationMs:Date.now()-started}))
 await page.keyboard.press('Escape')
}
async function home(entryMode){
 const started=Date.now()
 await page.evaluate(()=>window.ng.getComponent(document.querySelector('app-root')).onClickHome())
 await ready('web_desktop')
 report.steps.push({action:'shell-home',entryMode,durationMs:Date.now()-started,state:await inspect()})
}
try{
 await page.goto(`${client}/?webspace_id=${encodeURIComponent(webspace)}&scenario_id=applications`,{waitUntil:'domcontentloaded',timeout:90000})
 await ready('applications')
 await scenario('builder');await picker()
 await home('explicit-scenario')
 await scenario('builder');await picker()
 // Opening without a scenario never created a startup-preparation record.
 // A later normal URL must not manufacture one and block the next Home.
 await page.goto(`${client}/?webspace_id=${encodeURIComponent(webspace)}`,{waitUntil:'domcontentloaded',timeout:90000})
 // The isolated host's manifest Home is web_desktop. An unaddressed entry
 // resolves that Home, not the previous tab's last Builder selection.
 await ready('web_desktop')
 report.unaddressedEntry={state:await inspect(),startup:await page.evaluate(()=>
  window.ng.getComponent(document.querySelector('app-root')).ydoc.getStartupNavigationPreparationSnapshot())}
 assert.ok(report.unaddressedEntry.startup.active!==true || report.unaddressedEntry.startup.status==='prepared',
  'unaddressed startup has not settled')
 await scenario('builder')
 report.postNavigationStartup=await page.evaluate(()=>window.ng.getComponent(document.querySelector('app-root'))
  .ydoc.getStartupNavigationPreparationSnapshot())
 assert.equal(report.postNavigationStartup.active,false,'ordinary navigation rearmed an obsolete startup intent')
 await home('unaddressed')
 await scenario('builder');await picker()
 assert.equal(report.snapshotRequests,0);assert.equal(report.repairRequests,0)
 assert.deepEqual(report.failedReads,[])
 assert.deepEqual(report.navigationRejections,[])
 assert.deepEqual(report.errors,[])
 report.passed=true
}catch(error){
 report.passed=false;report.errors.push(String(error.stack || error));report.failureState=await inspect().catch(()=>null)
}finally{
 report.finishedAt=new Date().toISOString()
 await fs.writeFile(path.join(output,'home-builder-report.json'),JSON.stringify(report,null,2))
 await browser.close()
}
console.log(JSON.stringify(report))
if(!report.passed)process.exitCode=1
