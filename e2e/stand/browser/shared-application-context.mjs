import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
import {chromium} from './node_modules/playwright/index.mjs'

const api=process.env.ADAOS_E2E_API, client=process.env.ADAOS_E2E_CLIENT
const token=process.env.ADAOS_E2E_HUB_TOKEN, webspace=process.env.ADAOS_E2E_WEBSPACE
const subnet=process.env.ADAOS_E2E_SUBNET_ID
const selections=JSON.parse(process.env.ADAOS_E2E_RUNTIME_SELECTIONS || '[]')
const requireInitial=process.env.ADAOS_E2E_REQUIRE_INITIAL_OBSERVATION!=='0'
const provider=selections.find(item=>item.application_id==='subscription_status')
assert.ok(token && webspace?.startsWith('desktop-codex-') && provider,
 'isolated host with the real shared provider selection required')
const output=path.resolve(process.env.ADAOS_E2E_ARTIFACT_DIR)
await fs.mkdir(output,{recursive:true})
const report={startedAt:new Date().toISOString(),webspace,provider,initialObservationRequired:requireInitial,errors:[],responses:[]}
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({viewport:{width:1440,height:960},serviceWorkers:'block'})
await context.addInitScript(({api,token,webspace,subnet})=>{
 window.__ADAOS_BASE__=api;window.__ADAOS_TOKEN__=token
 for(const [key,value] of Object.entries({adaos_device_id:'e2e-shared-context',adaos_webspace_id:webspace,
  adaos_hub_base:api,adaos_local_hub_base:api,adaos_try_local_hub:'1',adaos_hub_token:token,
  adaos_local_subnet_id:subnet,adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'en'}))localStorage.setItem(key,value)
},{api,token,webspace,subnet})
const page=await context.newPage()
const requestStarts=new WeakMap()
page.on('request',request=>requestStarts.set(request,Date.now()))
const pendingResponses=new Set()
page.on('pageerror',error=>report.errors.push(error.message.slice(0,500)))
page.on('response',response=>{
 const pathname=new URL(response.url()).pathname
 if(!pathname.endsWith('/tools/call') && !pathname.startsWith('/api/services/context_chat_service/'))return
 const task=(async()=>{
  const body=response.request().postDataJSON()
  const entry={path:pathname,tool:body?.tool,applicationId:body?.context?.application_id,
   status:response.status(),release:response.headers()['x-adaos-release-digest'],
   durationMs:Date.now()-(requestStarts.get(response.request()) || Date.now())}
  if(response.status()>=400){
   const data=await response.json().catch(()=>({}));const detail=data.detail || data
   entry.code=typeof detail==='string'?detail.slice(0,200):detail.error || detail.code || 'unknown'
  }
  report.responses.push(entry)
 })().catch(error=>report.errors.push(String(error).slice(0,300)))
 pendingResponses.add(task);task.finally(()=>pendingResponses.delete(task))
})
try{
 const started=Date.now()
 await page.goto(`${client}/?webspace_id=${webspace}&scenario_id=context_chat`,{waitUntil:'domcontentloaded'})
 await page.waitForFunction(()=>{
  if(!document.querySelector('ada-desktop') || !document.querySelector('app-root'))return false
  const app=window.ng?.getComponent(document.querySelector('app-root'))
  const desktop=window.ng?.getComponent(document.querySelector('ada-desktop'))
  return desktop?.pageSchema?._adaos?.component?.id==='context_chat' &&
   app.ydoc.getSyncRuntimeSnapshot().projectionConvergence?.state==='applied'
 },null,{timeout:90000})
 report.projectionReadyMs=Date.now()-started
 console.log(JSON.stringify({phase:'projection-ready',durationMs:report.projectionReadyMs}))
 await page.waitForFunction(()=>{
  if(!document.querySelector('ada-project-list-widget'))return false
  const session=window.ng?.getComponent(document.querySelector('ada-project-list-widget'))?.s
  return session && !session.loading
 },null,{timeout:90000})
 report.chat=await page.evaluate(()=>{
  const session=window.ng.getComponent(document.querySelector('ada-project-list-widget')).s
  return {loading:session.loading,error:session.error,projectCount:session.projects.length,
   connected:!!session.account.connected,planEnabled:!!session.account.plan_enabled,
   managementApplication:session.account.management_application}
 })
 report.chatReadyMs=Date.now()-started
 console.log(JSON.stringify({phase:'chat-data-ready',durationMs:report.chatReadyMs}))
 assert.equal(report.chat.error,'','native Chat data failed to load')
 assert.ok(report.chat.managementApplication,'Chat did not expose the Core connection manager')
 report.modalDefinitions=await page.evaluate(()=>{
  const node=window.ng.getComponent(document.querySelector('app-root')).ydoc
  const definitions=node.toJSON(node.getPath('ui/application/modals')) || {}
  return Object.entries(definitions).filter(([id])=>id.includes('subscription_status_modal')).map(([id,value])=>({
   id,version:value?.version,sourceAuthority:value?._adaos?.sourceAuthority,
   initialActions:(value?.schema?.autoActions || []).map(entry=>entry.action?.target),
  }))
 })
 await page.getByTestId('account-button').click()
 const mount=requireInitial ? page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/tools/call') &&
  response.request().postDataJSON()?.tool==='subscription_status_skill:get_status',{timeout:60000}).catch(()=>null) : null
 await page.getByTestId('manage-ai-connection').click()
 const actions=page.locator('[data-webui-widget-id="subscription-status-actions"]')
 const refreshButton=actions.locator('[data-command-id="refresh"]')
 await refreshButton.waitFor({state:'visible',timeout:30000})
 if(mount){
  const mounted=await mount
  assert.ok(mounted,'shared modal did not load its initial subscription observation')
  await mounted.finished()
  assert.equal(mounted.status(),200,'shared modal initial observation failed inside Chat')
  report.initialObservation='passed'
 }
 const refresh=page.waitForResponse(response=>{
  if(!new URL(response.url()).pathname.endsWith('/tools/call'))return false
  return response.request().postDataJSON()?.tool==='subscription_status_skill:refresh_status'
 },{timeout:60000}).catch(()=>null)
 const clicked=Date.now()
 await refreshButton.click()
 const response=await refresh
 assert.ok(response,'shared refresh receipt was not observed')
 report.refreshMs=Date.now()-clicked
 await response.finished()
 assert.equal(response.status(),200,'shared Subscription refresh failed inside Chat')
 assert.equal(response.headers()['x-adaos-release-digest'],provider.release_digest,
  'shared refresh did not execute the host-selected provider release')
 const request=response.request().postDataJSON()
 assert.equal(request.context.application_id,'context_chat','test lost the embedding Application context')
 report.refresh={status:response.status(),callerApplication:request.context.application_id,
  providerRelease:response.headers()['x-adaos-release-digest']}
 await page.waitForFunction(()=>{
  const desktop=window.ng.getComponent(document.querySelector('ada-desktop'))
  const node=desktop?.ydoc || window.ng.getComponent(document.querySelector('app-root')).ydoc
  return Boolean(node.doc?.getMap('data')?.get('subscription_ai_connection'))
 },null,{timeout:30000})
 await page.waitForTimeout(2500)
 await Promise.all([...pendingResponses])
 assert.equal(report.responses.filter(item=>item.status>=400).length,0,'Chat or shared modal had failed reads/actions')
 assert.ok(report.responses.some(item=>item.path.endsWith('/ui/api/state')&&item.status===200),
  'native service state read was not observed')
 report.result='passed'
}catch(error){
 report.errors.push(String(error.stack || error).slice(0,2000))
 report.failure=await page.evaluate(()=>{
  const element=document.querySelector('ada-desktop'), widget=document.querySelector('ada-project-list-widget')
  const desktop=element && window.ng?.getComponent(element)
  const session=widget && window.ng?.getComponent(widget)?.s
  return {scenario:desktop?.pageSchema?._adaos?.component?.id,loading:session?.loading,error:session?.error,
   widgets:[...document.querySelectorAll('[data-webui-widget-id]')].map(node=>node.getAttribute('data-webui-widget-id'))}
 }).catch(()=>null)
}finally{
 await Promise.all([...pendingResponses])
 report.finishedAt=new Date().toISOString()
 await fs.writeFile(path.join(output,'shared-context-report.json'),JSON.stringify(report,null,2))
 await browser.close()
}
console.log(JSON.stringify(report))
if(report.errors.length || report.result!=='passed')process.exitCode=1
