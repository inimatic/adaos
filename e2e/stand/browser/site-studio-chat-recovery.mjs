// Requires local hub authority and an explicitly selected Site Studio Trial.
// Chat execution is fixture-backed; project reads and the isolated site preview are real.
import {createRequire} from 'node:module'
import fs from 'node:fs/promises'
const require=createRequire(process.cwd()+'/e2e/stand/browser/entry.mjs'),{chromium,expect}=require('@playwright/test')
const app=process.env.ADAOS_E2E_SCENARIO_ID||'site_studio_test_20261003_e2eadc45d49732_26215830',pid=process.env.ADAOS_E2E_PROJECT_ID||'project-0f28c891b3724676a538a129fa53114b'
const token=process.env.ADAOS_E2E_HUB_TOKEN,release=process.env.ADAOS_E2E_EXPECT_RELEASE,output=process.env.ADAOS_E2E_OUTPUT
if(!token||!release||!output)throw Error('Explicit Candidate and local control authority required')
await fs.mkdir(output,{recursive:true})
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1600,height:1050},locale:'ru-RU'})
await context.addInitScript(({token})=>{
 if(location.origin!=='http://localhost:8100')return
 window.__ADAOS_DEBUG__=true;window.__ADAOS_BASE__='http://127.0.0.1:8777';window.__ADAOS_TOKEN__=token
 for(const[k,v]of Object.entries({adaos_device_id:'chat-recovery-qualification',adaos_webspace_id:'dev2',adaos_hub_base:'http://127.0.0.1:8777',adaos_local_hub_base:'http://127.0.0.1:8777',adaos_try_local_hub:'1',adaos_hub_token:token,adaos_local_subnet_id:'sn_6acf0c01',adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'ru'}))localStorage.setItem(k,v)
},{token})
const page=await context.newPage(),report={passed:false,checks:[],continuations:[],reads:[],errors:[],blockedWrites:[]}
let run='qa-recovered-existing',phase=0,resultText='Проверочный запрос восстановлен автоматически.'
let mode='recovery',fixtureMessages=[]
const fulfill=(route,result)=>route.fulfill({status:200,contentType:'application/json',headers:{'x-adaos-release-digest':release},body:JSON.stringify({ok:true,result})})
page.on('pageerror',e=>report.errors.push(e.message))
await page.addLocatorHandler(page.getByRole('button',{name:'Продолжить проверку',exact:true}),async l=>l.click())
await page.route('**/api/tools/call',async route=>{
 const data=route.request().postDataJSON(),tool=String(data?.tool||'')
 if(tool.endsWith(':query_authoring_run')){
  report.reads.push({tool,project_id:data.arguments?.project_id})
  return fulfill(route,{ok:true,status:phase<2?'pending':'idle',job_id:phase<2?run:'',project_id:pid})
 }
 if(tool.endsWith(':continue_authoring')){
  const target=data.arguments?.job_id
  report.continuations.push({job_id:target,project_id:data.arguments?.project_id,mode})
  if(target!==run||data.arguments.project_id!==pid)throw Error('Continuation targeted a different run/project')
  phase++
  if(phase>=2)fixtureMessages=[{id:run+'-response',from:'assistant',text:resultText,ts:Date.now(),job_id:run,
    progress_group_id:run,progress_phase:'review',progress_label:'Результат',progress_status:'completed',progress_seq:1,actions:[]}]
  return fulfill(route,{ok:true,status:phase>=2?'completed':'pending',job_id:run,project_id:pid})
 }
 if(tool.endsWith(':discuss')){
  if(mode!=='send')throw Error('A recovery check must not submit a new message')
  return fulfill(route,{ok:true,status:'pending',job_id:run,project_id:pid})
 }
 if(/:(?:mutate_|publish|prepare_|commit_|request_domain|activate_domain|select_project|create_project)/.test(tool)){
  report.blockedWrites.push(tool);return route.fulfill({status:409,body:'diagnostic_read_only'})
 }
 let response
 try{response=await route.fetch()}
 catch{if(!page.isClosed())report.errors.push('Diagnostic read transport failed');await route.abort().catch(()=>{});return}
 if(tool.endsWith(':query_discussion')&&fixtureMessages.length){
  const body=await response.json(),result=body.result||body
  result.messages=[...(result.messages||[]),...fixtureMessages]
  return route.fulfill({response,json:body})
 }
 return route.fulfill({response})
})
const url='http://localhost:8100/?scenario_id='+app+'&webspace_id=dev2&webspace=dev2&try_local_hub=1'
const chat=()=>page.locator('[data-webui-widget-id="site_discussion"]').last()
try{
 await page.goto(url,{waitUntil:'domcontentloaded',timeout:90000})
 await expect(chat()).toContainText(resultText,{timeout:140000})
 expect(report.continuations.filter(x=>x.mode==='recovery').length).toBe(2)
 report.checks.push('A fresh page discovers its persisted pending run and completes it automatically; no discuss call')
 mode='reload';run='qa-recovered-after-reload';phase=0;fixtureMessages=[];resultText='Проверочный запрос продолжен после обновления страницы.'
 await page.reload({waitUntil:'domcontentloaded',timeout:90000})
 await expect(chat()).toContainText(resultText,{timeout:140000})
 expect(report.continuations.filter(x=>x.mode==='reload').length).toBe(2)
 report.checks.push('Browser reload recovers and continues the same owned run without resending a user message')
 mode='send';run='qa-new-send';phase=0;fixtureMessages=[];resultText='Проверочная отправка завершена автоматически.'
 await chat().locator('textarea').fill('[Проверка интерфейса без обращения к модели]')
 await chat().locator('textarea').press('Enter')
 await expect(chat()).toContainText(resultText,{timeout:140000})
 expect(report.continuations.filter(x=>x.mode==='send').length).toBe(2)
 report.checks.push('A new send pins continuation to the returned job ID and reaches a visible Result')
 const preview=page.locator('[data-webui-widget-id="site_preview"]').last()
 await expect(preview).toContainText('Applied',{timeout:100000})
 report.checks.push('Actual Foundation preview renders in the isolated public runtime with authenticated media')
 await page.screenshot({path:output+'/chat-and-preview.png'})
 expect(report.errors).toEqual([])
 report.passed=true
}catch(e){report.error=e.message;await page.screenshot({path:output+'/failure.png'}).catch(()=>{})}
finally{
 report.disclosure='Only async chat execution/results are browser fixtures. Native project selection, data sources and isolated preview/media are real. No provider calls or durable user messages are made by this browser check.'
 await page.unrouteAll({behavior:'ignoreErrors'})
 await browser.close()
 await fs.writeFile(output+'/report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report))
}
if(!report.passed)process.exitCode=1
