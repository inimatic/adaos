import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import {chromium} from './node_modules/playwright/index.mjs'

// Qualify an ambiguous legacy component without choosing/deleting either owner.
// Export authority/status observations only, never workbench text or user data.
const token=process.env.ADAOS_E2E_HUB_TOKEN, webspace=process.env.ADAOS_E2E_WEBSPACE
const release=process.env.ADAOS_E2E_RELEASE
assert.ok(token && webspace?.startsWith('desktop-codex-') && release)
const output=path.resolve(process.env.ADAOS_E2E_ARTIFACT_DIR)
await fs.mkdir(output,{recursive:true})
const report={startedAt:new Date().toISOString(),responses:[],errors:[]}
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({serviceWorkers:'block'})
await context.addInitScript(({token,webspace})=>{
 window.__ADAOS_BASE__='http://127.0.0.1:8777';window.__ADAOS_TOKEN__=token
 for(const [key,value] of Object.entries({adaos_device_id:'e2e-builder-authority',adaos_webspace_id:webspace,
  adaos_hub_base:window.__ADAOS_BASE__,adaos_local_hub_base:window.__ADAOS_BASE__,adaos_try_local_hub:'1',adaos_hub_token:token,
  adaos_local_subnet_id:'sn_6acf0c01',adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'en'}))localStorage.setItem(key,value)
},{token,webspace})
const page=await context.newPage(), pending=new Set()
page.on('pageerror',error=>report.errors.push(error.message))
page.on('response',response=>{
 if(!new URL(response.url()).pathname.endsWith('/tools/call'))return
 const task=(async()=>{
  const request=response.request().postDataJSON()
  const record={tool:request?.tool,intent:request?.intent,status:response.status()}
  if(request?.tool?.endsWith(':get_workbench') && response.ok()){
   const body=await response.json()
   record.ownershipStatus=body.result?.ownership?.status
   record.commandsEnabled=Object.values(body.result?.commands||{}).some(Boolean)
  }
  report.responses.push(record)
 })().catch(error=>report.errors.push(String(error)))
 pending.add(task);task.finally(()=>pending.delete(task))
})
try{
 await page.goto(`http://127.0.0.1:8100/?webspace_id=${webspace}&scenario_id=builder`,{waitUntil:'domcontentloaded'})
 await page.waitForFunction(release=>{
  const node=document.querySelector('ada-desktop'),root=document.querySelector('app-root')
  if(!node||!root)return false
  const desktop=window.ng?.getComponent(node),app=window.ng?.getComponent(root)
  const live=app?.ydoc?.getMaterializationSnapshot?.()
  return desktop?.pageSchema?._adaos?.component?.id==='builder' && live?.ready &&
   live.applicationReleaseDigest===release &&
   document.querySelector('[data-webui-widget-id="design-current-work"]')?.getAttribute('data-webui-data-has-value')==='true'
 },release,{timeout:90000})
 await Promise.all([...pending])
 report.widget=await page.locator('[data-webui-widget-id="design-current-work"]').evaluate(node=>({
  state:node.getAttribute('data-webui-data-state'),hasValue:node.getAttribute('data-webui-data-has-value')}))
 const workbench=report.responses.find(item=>item.tool?.endsWith(':get_workbench'))
 assert.ok(workbench,'the selected workbench did not finish its SDK read')
 assert.equal(workbench.ownershipStatus,'ambiguous')
 assert.equal(workbench.commandsEnabled,false,'ambiguous source enabled an unqualified command')
 assert.equal(report.responses.some(item=>item.status>=400),false,'an active SDK read failed')
 assert.equal(report.widget.hasValue,'true')
 report.releaseDigest=release
 report.passed=true
}catch(error){report.errors.push(String(error.stack||error))}
finally{
 report.finishedAt=new Date().toISOString()
 await fs.writeFile(path.join(output,'stable-read-audit.json'),JSON.stringify(report,null,2))
 await browser.close()
}
console.log(JSON.stringify({passed:report.passed,errors:report.errors}))
if(report.errors.length)process.exitCode=1
