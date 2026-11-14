// Private local Beta: exact immutable release, real broker/Root data, both layouts.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import {chromium} from './node_modules/playwright/index.mjs'
import {installSystemOverlayDismissal,dismissSystemOverlays} from './prototype-navigation.mjs'

const token=process.env.ADAOS_E2E_HUB_TOKEN,host=process.env.ADAOS_E2E_WEBSPACE
const release=process.env.ADAOS_E2E_RELEASE,output=process.env.ADAOS_E2E_OUTPUT
const mode=process.env.ADAOS_E2E_DISTRIBUTION_MODE||'blocked'
const publish=process.env.ADAOS_E2E_PUBLISH_PLAN==='1'
assert.ok(['blocked','ready','verified'].includes(mode) && (!publish||mode==='ready'))
assert.ok(token && host?.startsWith('desktop-codex-infrastructure-beta-') && release && output)
await fs.mkdir(output,{recursive:true})
const report={passed:false,release,host,mode,cases:[],toolResponses:[],errors:[]}
const safeError=e=>String(e?.message||e).split('\n')[0].replaceAll(token,'[redacted]').slice(0,300)
const browser=await chromium.launch({headless:true})
let page
try {
 const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}})
 await context.addInitScript(({token,host})=>{
  window.__ADAOS_BASE__='http://127.0.0.1:8777';window.__ADAOS_TOKEN__=token
  for(const [key,value] of Object.entries({adaos_device_id:'e2e-infrastructure-distribution-beta',adaos_webspace_id:host,
   adaos_local_hub_url:window.__ADAOS_BASE__,adaos_local_hub_token:token,adaos_hub_base:window.__ADAOS_BASE__,
   adaos_hub_token:token,adaos_try_local_hub:'1',adaos_local_subnet_id:'sn_6acf0c01',
   adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'ru'}))localStorage.setItem(key,value)
 },{token,host})
 page=await context.newPage()
 await installSystemOverlayDismissal(page)
 page.on('pageerror',e=>report.errors.push(safeError(e)))
 page.on('response',response=>{
  if(!new URL(response.url()).pathname.endsWith('/tools/call'))return
  const request=response.request().postDataJSON()
  if(request?.tool?.startsWith('infra_ops_skill:'))report.toolResponses.push({tool:request.tool,status:response.status()})
 })
 const headers={'X-AdaOS-Token':token}
 const opened=await context.request.post(`http://127.0.0.1:8777/api/node/yjs/webspaces/${host}/scenario`,{
  headers,data:{scenario_id:'infra_ops',wait_for_rebuild:true,request_source:'infrastructure.distribution.beta.browser'},timeout:180000})
 assert.ok(opened.ok(),'Application navigation rejected')
 const url=new URL('http://127.0.0.1:8100/')
 for(const [k,v] of Object.entries({intent:'webspace.open',zone:'lo',subnet_id:'sn_6acf0c01',webspace_id:host,
  scenario_id:'infra_ops',expected_scenario_id:'infra_ops',try_local_hub:'1'}))url.searchParams.set(k,v)
 await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:60000})
 await page.waitForFunction(release=>{
  const desktop=document.querySelector('ada-desktop'),root=document.querySelector('app-root')
  const view=desktop&&window.ng?.getComponent(desktop)
  const app=root&&window.ng?.getComponent(root)
  const snapshot=app?.ydoc?.getMaterializationSnapshot?.()
  return view?.pageSchema?._adaos?.component?.id==='infra_ops' && snapshot?.ready && snapshot.applicationReleaseDigest===release
 },release,{timeout:120000})
 const data=await context.request.post('http://127.0.0.1:8777/api/tools/call',{headers,
  data:{tool:'infra_ops_skill:distribution_snapshot',arguments:{},context:{webspace_id:host,scenario_id:'infra_ops'},intent:'read'},timeout:60000})
 assert.ok(data.ok(),'Distribution snapshot rejected')
 const model=(await data.json()).result
 assert.equal(model.plans[0].state,mode==='blocked'?'blocked':'ready')
 assert.equal(model.builds.length,3)
 assert.equal(model.builds.find(r=>r.id==='linux-x86_64-gnu')?.state,'ready','Qualified Linux kit missing')
 if(mode!=='blocked')assert.ok(model.builds.every(r=>r.state==='ready'),'A planned kit remains unqualified')
 if(mode==='verified'){
  for(const build of model.builds)assert.ok(model.publications.some(r=>r.profile===build.id&&r.state==='verified'),'Verified regional receipt missing')
 }
 assert.ok(model.sources.some(r=>r.id==='ru-stand'&&r.state==='fresh'),'Real stand source not connected')
 assert.ok(model.sources.some(r=>r.id==='publication-worker'&&r.state==='fresh'),'Scheduled worker not fresh')
 assert.ok(model.demand.length>0 && model.failures.length>0,'Real installation facts missing')
 const publicText=JSON.stringify(model)
 assert.ok(!publicText.includes(token) && !/private_recovery|BEGIN .*PRIVATE KEY|REG_RU_S3_SECRET/.test(publicText),'Private custody leaked')
 await fs.writeFile(path.join(output,'distribution-model.json'),JSON.stringify(model,null,2))
 if(mode==='blocked'){
 const blocked=await context.request.post('http://127.0.0.1:8777/api/tools/call',{headers,
  data:{tool:'infra_ops_skill:publish_distribution',request_id:`infra-beta-blocked-${Date.now()}`,
   arguments:{expected_plan_sha256:model.plans[0].plan_sha256},context:{webspace_id:host,scenario_id:'infra_ops'},intent:'mutation'},timeout:60000})
 assert.ok(blocked.ok(),'Admitted publisher could not reach the plan controller')
 assert.equal((await blocked.json()).result?.error,'planned_artifacts_not_ready')
 report.incompletePlanRefused=true
 }
 for(const [layout,viewport] of Object.entries({wide:{width:1440,height:1000},compact:{width:390,height:844}})){
  await page.setViewportSize(viewport)
  for(const [section,widget] of Object.entries({overview:'infra-targets',backups:'infra-backups',incidents:'infra-incidents',journal:'infra-journal',checks:'infra-checks',distribution:'infra-distribution-plans'})){
   await page.locator(`[data-webui-widget-id="infra-sections"] [data-command-id="${section}"]`).click()
   await page.locator(`[data-webui-widget-id="${widget}"]`).waitFor({state:'visible',timeout:30000})
   report.cases.push({layout,section,visible:true})
   if(section==='distribution'){
    await page.waitForFunction(()=>document.querySelector('[data-webui-widget-id="infra-distribution-builds"]')?.textContent.includes('aarch64'),null,{timeout:45000})
    for(const id of ['sources','plans','builds','publications','demand','failures','batches']){
     assert.ok(await page.locator(`[data-webui-widget-id="infra-distribution-${id}"]`).isVisible(),`${id} table missing`)
    }
    const button=page.locator('[data-webui-widget-id="infra-distribution-plans"] [data-command-id="publish_all"]')
    if(mode==='blocked')assert.equal(await button.count(),0,'Incomplete plan exposed publication action')
    else if(!report.publication){
     assert.equal(await button.count(),1,'Ready whole-plan action missing')
     // Queue from the last view so the browser closes while the separately
     // scheduled worker still owns the actual provider execution.
     if(publish&&layout==='compact'){
      const response=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/tools/call')&&r.request().postDataJSON()?.tool==='infra_ops_skill:publish_distribution',{timeout:60000})
      await button.click()
      const received=await response,body=await received.json()
      assert.ok(received.ok()&&body.result?.ok&&body.result?.batch_id,'Whole-plan UI submission failed')
      report.publication={batch_id:body.result.batch_id,state:body.result.state,plan_sha256:model.plans[0].plan_sha256}
      await fs.writeFile(path.join(output,'publication-request.json'),JSON.stringify(report.publication,null,2))
     }
    }
    await dismissSystemOverlays(page)
    await page.screenshot({path:path.join(output,`${layout}-distribution.png`),fullPage:true})
    for(const id of ['builds','publications','demand','failures']){
     const table=page.locator(`[data-webui-widget-id="infra-distribution-${id}"]`)
     await table.screenshot({path:path.join(output,`${layout}-${id}.png`)})
     if(layout==='compact'){
      // Tall card collections can render only their viewport during a full
      // element capture. Keep an actual visible mobile view as well.
      await table.evaluate(element=>element.scrollIntoView({block:'start'}))
      await page.waitForTimeout(200)
      await page.screenshot({path:path.join(output,`${layout}-${id}-viewport.png`)})
     }
    }
   }
  }
 }
 assert.ok(report.toolResponses.some(r=>r.tool==='infra_ops_skill:distribution_snapshot'&&r.status===200))
 assert.ok(!report.toolResponses.some(r=>r.status>=400),'Application tool call failed')
 report.passed=report.errors.length===0
}catch(error){
 report.errors.push(safeError(error))
 if(page)await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{})
}finally{
 report.finishedAt=new Date().toISOString()
 await fs.writeFile(path.join(output,'browser.json'),JSON.stringify(report,null,2))
 await browser.close()
}
console.log(JSON.stringify({passed:report.passed,cases:report.cases.length,errors:report.errors}))
if(!report.passed)process.exitCode=1
