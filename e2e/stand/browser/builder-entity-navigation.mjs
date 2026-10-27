import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
import {chromium} from './node_modules/playwright/index.mjs'

const token=process.env.ADAOS_E2E_HUB_TOKEN
const webspace=process.env.ADAOS_E2E_WEBSPACE
const expectedRelease=process.env.ADAOS_E2E_RELEASE
assert.ok(token && webspace?.startsWith('desktop-codex-') && expectedRelease)
const output=path.resolve(process.env.ADAOS_E2E_ARTIFACT_DIR || 'e2e/artifacts/builder/navigation-20261027')
await fs.mkdir(output,{recursive:true})
const report={errors:[],requests:[],samples:[],startedAt:new Date().toISOString()}
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({viewport:{width:1440,height:960},serviceWorkers:'block'})
await context.addInitScript(({token,webspace})=>{
 window.__ADAOS_BASE__='http://127.0.0.1:8777';window.__ADAOS_TOKEN__=token
 for(const [key,value] of Object.entries({adaos_device_id:'e2e-builder-entity',adaos_webspace_id:webspace,
  adaos_hub_base:window.__ADAOS_BASE__,adaos_local_hub_base:window.__ADAOS_BASE__,adaos_try_local_hub:'1',adaos_hub_token:token,
  adaos_local_subnet_id:'sn_6acf0c01',adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'en'}))localStorage.setItem(key,value)
 window.__builderEntitySamples=[]
 function sample(){
  const desktop=window.ng?.getComponent(document.querySelector('ada-desktop'))
  if(desktop?.pageSchema?._adaos?.component?.id==='builder'){
   const state=desktop.pageState.getSnapshot(), previous=window.__builderEntitySamples.at(-1)
   const value={project:state.selectedProjectId,kind:state.selectedProjectKind,title:state.applicationTitle,url:location.search}
   if(!previous||JSON.stringify(previous)!==JSON.stringify(value))window.__builderEntitySamples.push(value)
  }
  requestAnimationFrame(sample)
 }
 requestAnimationFrame(sample)
},{token,webspace})
const page=await context.newPage()
const requestTimes=new Map()
page.on('pageerror',e=>report.errors.push(e.message))
page.on('request',request=>{
 if(!new URL(request.url()).pathname.endsWith('/tools/call'))return
 const body=request.postDataJSON()
 const entry={tool:body.tool,intent:body.intent,objectId:body.arguments?.object_id}
 requestTimes.set(request,{started:Date.now(),entry})
 report.requests.push(entry)
 if(body.tool?.endsWith(':list_projects'))console.log(JSON.stringify({phase:'picker-request',at:Date.now()}))
})
page.on('response',response=>{
 const timed=requestTimes.get(response.request())
 if(timed)Object.assign(timed.entry,{status:response.status(),durationMs:Date.now()-timed.started})
})
const href=`http://127.0.0.1:8100/?webspace_id=${webspace}&scenario_id=builder`
async function waitProject(id){
 await page.waitForFunction(({id,release})=>{
  const desktop=window.ng?.getComponent(document.querySelector('ada-desktop'))
  const app=window.ng?.getComponent(document.querySelector('app-root'))
  const materialization=app?.ydoc?.getMaterializationSnapshot?.()
  const query=new URL(location.href).searchParams
  return desktop?.pageSchema?._adaos?.component?.id==='builder' && materialization?.ready &&
   materialization.applicationReleaseDigest===release &&
   desktop.pageState.getSnapshot().selectedProjectId===id && query.get('builder_object_id')===id
 },{id,release:expectedRelease},{timeout:90000})
}
try{
 const started=Date.now()
 await page.goto(href,{waitUntil:'domcontentloaded',timeout:90000})
 await waitProject('natural_retouch')
 report.restoredProjectMs=Date.now()-started
 report.samples=await page.evaluate(()=>window.__builderEntitySamples)
 report.restoredTitle=report.samples.at(-1).title
 assert.ok(report.samples.every(sample=>sample.project==='natural_retouch'),'another seed project was rendered before restoration')
 assert.ok(report.samples.every(sample=>sample.title && sample.title!=='Builder'),'the visible header rendered another project seed')
 const opened=Date.now()
 assert.equal(await page.evaluate(()=>window.ng.getComponent(document.querySelector('ada-desktop')).pageActions.handle(
  {type:'openModal',params:{modalId:'project-picker'}})),true)
 const picker=page.locator('[data-webui-widget-id$="project-picker-table"]')
 await picker.locator('tr[data-record-id]').first().waitFor({state:'visible',timeout:30000})
 report.pickerMs=Date.now()-opened
 report.picker={visibleRows:await picker.locator('tbody tr').count()}
 await page.screenshot({path:path.join(output,'project-picker.png')})
 // Exercise the real row action, including its asynchronous preview selection.
 report.restoredTitle=await page.evaluate(()=>window.ng.getComponent(document.querySelector('ada-desktop')).pageState.getSnapshot().applicationTitle)
 await picker.locator('input[type="search"]').fill('adaos_builder')
 const selectedAt=Date.now()
 await picker.locator('tr[data-record-id="project:adaos_builder"]').click({timeout:30000})
 await waitProject('adaos_builder')
 report.rowSelectionMs=Date.now()-selectedAt
 report.rowSelection='passed'
 const selectedEffects=report.requests.filter(request=>request.intent!=='read').length
 await page.goBack();await waitProject('natural_retouch')
 const restoredTitle=await page.evaluate(()=>window.ng.getComponent(document.querySelector('ada-desktop')).pageState.getSnapshot().applicationTitle)
 assert.equal(restoredTitle,report.restoredTitle,'history restored the entity id but retained another entity header')
 await page.waitForTimeout(5000);await waitProject('natural_retouch')
 await page.goForward();await waitProject('adaos_builder')
 assert.equal(report.requests.filter(request=>request.intent!=='read').length,selectedEffects,'history replayed the selected-row effect')
 report.rowSelectionHistory='passed'
 // An explicit application address is independent from its saved last-used project.
 await page.goto(href+'&builder_object_type=project&builder_object_id=builder',{waitUntil:'domcontentloaded'})
 await waitProject('builder')
 await page.waitForTimeout(5000)
 await waitProject('builder')
 report.explicitAddress='passed'
 // Change only local declarative navigation state; no business action is replayed.
 await page.evaluate(()=>window.ng.getComponent(document.querySelector('ada-desktop')).pageState.patch({
  selectedProjectKind:'scenario',selectedProjectId:'natural_retouch',selectedProjectTitle:'Retouch'}))
 await waitProject('natural_retouch')
 const before=report.requests.filter(request=>request.intent!=='read').length
 await page.goBack();await waitProject('builder')
 await page.goForward();await waitProject('natural_retouch')
 assert.equal(report.requests.filter(request=>request.intent!=='read').length,before,'history replayed an effect')
 report.history='passed'
 report.finalUrl=page.url()
 await page.screenshot({path:path.join(output,'entity-navigation.png')})
 assert.ok(report.pickerMs<10000,`interactive picker exceeded 10 seconds: ${report.pickerMs}`)
}catch(error){
 report.errors.push(String(error.stack||error))
 report.body=(await page.locator('body').innerText().catch(()=>'' )).slice(0,1200)
 report.widgetIds=await page.locator('[data-webui-widget-id]').evaluateAll(elements=>elements.map(element=>element.getAttribute('data-webui-widget-id'))).catch(()=>[])
 report.pickerDom=await page.locator('[data-webui-widget-id$="project-picker-table"]').evaluate(element=>element.outerHTML.slice(0,2200)).catch(()=>null)
 await page.screenshot({path:path.join(output,'entity-navigation-failure.png')}).catch(()=>{})
}finally{
 report.finishedAt=new Date().toISOString()
 await fs.writeFile(path.join(output,'browser-report.json'),JSON.stringify(report,null,2))
 await browser.close()
}
console.log(JSON.stringify({restoredProjectMs:report.restoredProjectMs,pickerMs:report.pickerMs,picker:report.picker,
 rowSelection:report.rowSelection,rowSelectionHistory:report.rowSelectionHistory,
 explicitAddress:report.explicitAddress,history:report.history,errors:report.errors}))
if(report.errors.length)process.exitCode=1
