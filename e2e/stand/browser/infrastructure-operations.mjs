// Company-only Infrastructure Beta: real selected release, live reads and incident UI.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import {chromium} from './node_modules/playwright/index.mjs'
import {installSystemOverlayDismissal,dismissSystemOverlays} from './prototype-navigation.mjs'

const token=process.env.ADAOS_E2E_HUB_TOKEN, host=process.env.ADAOS_E2E_WEBSPACE
const release=process.env.ADAOS_E2E_RELEASE, output=process.env.ADAOS_E2E_OUTPUT
assert.ok(token && host?.startsWith('desktop-codex-infrastructure-beta-') && release && output)
await fs.mkdir(output,{recursive:true})
const report={passed:false,release,host,cases:[],toolResponses:[],errors:[]}
const safeError=error=>String(error?.message||error).split('\n')[0].replaceAll(token,'[redacted]').slice(0,300)
const browser=await chromium.launch({headless:true})
let page
try{
 const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}})
 await context.addInitScript(({token,host})=>{
  window.__ADAOS_BASE__='http://127.0.0.1:8777';window.__ADAOS_TOKEN__=token
  for(const [key,value] of Object.entries({adaos_device_id:'e2e-infrastructure-beta',adaos_webspace_id:host,
   adaos_local_hub_url:window.__ADAOS_BASE__,adaos_local_hub_token:token,adaos_hub_base:window.__ADAOS_BASE__,
   adaos_hub_token:token,adaos_try_local_hub:'1',adaos_local_subnet_id:'sn_6acf0c01',
   adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'ru'}))localStorage.setItem(key,value)
 },{token,host})
 page=await context.newPage()
 await installSystemOverlayDismissal(page)
 page.on('pageerror',error=>report.errors.push(safeError(error)))
 page.on('response',async response=>{
  if(!new URL(response.url()).pathname.endsWith('/tools/call'))return
  const request=response.request().postDataJSON()
  if(!request?.tool?.startsWith('infra_ops_skill:'))return
  report.toolResponses.push({tool:request.tool,status:response.status()})
 })
 const opened=await context.request.post(`http://127.0.0.1:8777/api/node/yjs/webspaces/${host}/scenario`,{
  headers:{'X-AdaOS-Token':token},data:{scenario_id:'infra_ops',wait_for_rebuild:true,request_source:'infrastructure.beta.browser'},timeout:180000})
 assert.ok(opened.ok(),'isolated Application navigation was rejected')
 const url=new URL('http://127.0.0.1:8100/')
 for(const [key,value] of Object.entries({intent:'webspace.open',zone:'lo',subnet_id:'sn_6acf0c01',
  webspace_id:host,scenario_id:'infra_ops',expected_scenario_id:'infra_ops',try_local_hub:'1'}))url.searchParams.set(key,value)
 await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:60000})
 await page.waitForTimeout(3000)
 report.bootstrap=await page.evaluate(()=>({hasNg:!!window.ng,title:document.title,
  elements:['ada-desktop','app-root','ada-chat-widget'].map(tag=>({tag,present:!!document.querySelector(tag)})),
  widgetIds:[...document.querySelectorAll('[data-webui-widget-id]')].map(n=>n.getAttribute('data-webui-widget-id'))}))
 await fs.writeFile(path.join(output,'bootstrap.json'),JSON.stringify(report.bootstrap,null,2))
 await page.screenshot({path:path.join(output,'bootstrap.png'),fullPage:true})
 await page.waitForFunction(release=>{
  const desktop=document.querySelector('ada-desktop'),root=document.querySelector('app-root')
  const view=desktop&&window.ng?.getComponent(desktop),app=root&&window.ng?.getComponent(root)
  const snapshot=app?.ydoc?.getMaterializationSnapshot?.()
  return view?.pageSchema?._adaos?.component?.id==='infra_ops' && snapshot?.ready &&
   snapshot.applicationReleaseDigest===release
 },release,{timeout:120000})
 const target=page.locator('[data-webui-widget-id="infra-targets"]')
 await target.waitFor({state:'visible',timeout:60000})
 await page.waitForFunction(()=>document.querySelector('[data-webui-widget-id="infra-targets"]')?.textContent.includes('Global Root'),null,{timeout:60000})
 for(const [layout,viewport] of Object.entries({wide:{width:1440,height:1000},compact:{width:390,height:844}})){
  await page.setViewportSize(viewport)
  for(const [section,widget] of Object.entries({overview:'infra-targets',backups:'infra-backups',incidents:'infra-incidents',journal:'infra-journal',checks:'infra-checks'})){
   await page.locator(`[data-webui-widget-id="infra-sections"] [data-command-id="${section}"]`).click()
   const selected=page.locator(`[data-webui-widget-id="${widget}"]`)
   await selected.waitFor({state:'visible',timeout:30000})
   const expected={overview:['Global Root','Зональный Root','infra-01'],
    backups:['infrastructure.state','root.eu','root.ru'],incidents:['Root'],journal:['node:infra_ops'],
    checks:['backend.readiness','redis.memory']}[section]
   await page.waitForFunction(({widget,expected})=>{
    const node=document.querySelector(`[data-webui-widget-id="${widget}"]`)
    return node && expected.every(value=>node.textContent.includes(value))
   },{widget,expected},{timeout:30000})
   const text=await selected.innerText()
   assert.ok(!text.includes(token),'credential entered visible UI')
   assert.ok(!/private_recovery|BEGIN .*PRIVATE KEY|ROOT_TOKEN/.test(text),'private custody entered visible UI')
   if(section==='overview')assert.ok(text.includes('Global Root') && text.includes('Зональный Root') && text.includes('infra-01'))
   if(section==='backups')assert.ok(text.includes('infrastructure.state') && text.includes('root.eu') && text.includes('root.ru'))
   report.cases.push({layout,section,visible:true,characters:text.length})
   await dismissSystemOverlays(page)
   assert.ok(!await page.locator('.component-updates-backdrop').isVisible(),'system notice obscures visual evidence')
   await page.screenshot({path:path.join(output,`${layout}-${section}.png`),fullPage:true})
  }
 }
 assert.ok(report.toolResponses.some(r=>r.tool==='infra_ops_skill:snapshot'&&r.status===200))
 assert.ok(!report.toolResponses.some(r=>r.status>=400),'infrastructure read failed')
 await page.setViewportSize({width:1440,height:1000})
 await page.locator('[data-webui-widget-id="infra-sections"] [data-command-id="incidents"]').click()
 const acknowledgement=page.locator('[data-webui-widget-id="infra-incidents"] [data-command-id="acknowledge"]').first()
 if(await acknowledgement.count()){
  const response=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/tools/call')&&r.request().postDataJSON()?.tool==='infra_ops_skill:acknowledge')
  await acknowledgement.click()
  const received=await response,body=await received.json()
  assert.ok(received.ok()&&body.result?.ok,'UI incident acknowledgement was not accepted')
  report.uiAcknowledgement=true
 }else if(process.env.ADAOS_E2E_REQUIRE_ACK==='0'){
  report.uiAcknowledgement=false
  report.acknowledgementReason='Read-only repeat: no open incident; initial action receipt retained separately'
 }else{
  throw new Error('No open incident available to qualify the UI acknowledgement action')
 }
 report.passed=report.errors.length===0
}catch(error){
 report.errors.push(safeError(error))
 if(page){
  report.visibleWidgets=await page.locator('[data-webui-widget-id]').evaluateAll(nodes=>nodes.filter(n=>n.getClientRects().length).map(n=>n.getAttribute('data-webui-widget-id')))
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{})
 }
}
finally{
 report.finishedAt=new Date().toISOString()
 await fs.writeFile(path.join(output,'browser.json'),JSON.stringify(report,null,2))
 await browser.close()
}
console.log(JSON.stringify({passed:report.passed,cases:report.cases.length,errors:report.errors}))
if(!report.passed)process.exitCode=1
