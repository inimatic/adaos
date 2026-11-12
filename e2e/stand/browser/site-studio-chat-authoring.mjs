import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
// Connected acceptance: real model calls and reversible edits in an explicitly selected test project.
const APP = process.env.ADAOS_E2E_SCENARIO_ID
const project = process.env.ADAOS_E2E_SITE_PROJECT
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const client = process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/'
const previewOrigin = process.env.ADAOS_E2E_PREVIEW_ORIGIN || 'http://127.0.0.1:8102'
if (!APP || !project || !webspace || !subnet || !process.env.ADAOS_E2E_OUTPUT ||
    !process.env.ADAOS_E2E_HUB_TOKEN || process.env.ADAOS_E2E_ALLOW_REVERSIBLE_SITE_EDITS !== '1') {
  throw new Error('Explicit scenario/project/webspace/subnet/output/token and reversible-edit admission are required')
}
const token = process.env.ADAOS_E2E_HUB_TOKEN
const output = process.env.ADAOS_E2E_OUTPUT
await fs.mkdir(output, {recursive:true})
const browser = await chromium.launch({headless:true})
const context = await browser.newContext({viewport:{width:1600,height:1000},locale:'ru-RU'})
await context.addInitScript(({token, hub, webspace, subnet, origins}) => {
  if (!origins.includes(location.origin)) return
  window.__ADAOS_DEBUG__=true; window.__ADAOS_BASE__=hub; window.__ADAOS_TOKEN__=token
  for (const [key,value] of Object.entries({adaos_device_id:'site-studio-connected-beta',adaos_webspace_id:webspace,
    adaos_hub_base:hub,adaos_local_hub_base:hub,adaos_try_local_hub:'1',
    adaos_hub_token:token,adaos_local_subnet_id:subnet,adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'ru'})) localStorage.setItem(key,value)
}, {token,hub,webspace,subnet,origins:[new URL(client).origin,new URL(previewOrigin).origin]})
const page=await context.newPage()
page.setDefaultTimeout(20000)
const report={checks:[],errors:[],calls:[],candidate:null}
page.on('pageerror',error=>report.errors.push(error.message))
page.on('response',async response=>{
  if(!response.url().includes('/api/tools/call'))return
  let payload;try{payload=response.request().postDataJSON()}catch{return}
  if(!String(payload?.tool).startsWith(APP+'_skill'))return
  report.calls.push({tool:payload.tool,status:response.status(),runtime:response.headers()['x-adaos-runtime-source'],release:response.headers()['x-adaos-release-digest']})
  if(String(payload.tool).endsWith(':discuss')){
    const result=await response.json();report.job_id=(result.result??result).job_id;await fs.writeFile(output+'/chat-send-result.json',JSON.stringify(result,null,2))
  }
})
await page.addLocatorHandler(page.locator('.component-updates-backdrop'),async()=>{
  const close=page.locator('.component-updates-panel__tools button').last()
  if(await close.isVisible().catch(()=>false))await close.click()
})
const host=id=>page.locator(`[data-webui-widget-id="${id}"]`).last()
async function api(name,params={}){
 const id='beta-connected-'+crypto.randomUUID(); const response=await context.request.post(hub+'/api/tools/call',{headers:{'X-AdaOS-Token':token},data:{tool:APP+'_skill:'+name,arguments:params,dev:false,context:{webspace_id:webspace,scenario_id:APP,current_scenario_id:APP},request_id:id,idempotency_key:id,timeout:55}});
 const value=await response.json(); if(!response.ok()||value.ok===false)throw new Error(name+': '+JSON.stringify(value).slice(0,1000));return value.result??value;
}
let scope,before;
const pass=name=>{report.checks.push(name);console.log('PASS '+name)}
const url=new URL(client)
for(const [key,value] of Object.entries({webspace_id:webspace,webspace:webspace,scenario_id:APP,try_local_hub:'1'}))url.searchParams.set(key,value)
async function tool(id){
 const button=host('authoring_tools').locator(`[data-command-id="${id}"]:visible`).first()
 if(await button.count())await button.click()
 else {await host('authoring_tools').locator('button:not([data-command-id]):visible').last().click();await page.locator(`[data-command-id="${id}"]:visible`).last().click()}
}
try{
 await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:90000})
 await host('site_identity').waitFor({timeout:90000})
 await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({timeout:90000})
 await expect(host('site_identity').locator('[data-command-id="open-projects"]')).toHaveText('')
 await expect(host('site_identity').locator('[data-command-id="knowledge"]')).toHaveText('')
 pass('live-workbench-and-preview')
 const active=(await api('query_active_project')).item;scope={project_id:active.id,selection_revision:active.revision}; before=await api('query_preview',scope); if(active.id!==project)throw new Error('Wrong selected project');
 await tool('structure')
 await host('v_sections').getByText('Create an app.',{exact:true}).first().click()
 await tool('chat')
 const composer=host('site_discussion').locator('textarea').last()
 await composer.fill('Замени только заголовок выбранного главного экрана на «Create, test, evolve.». Остальные поля и разделы сохрани без изменений. Подготовь предложение для просмотра.')
 await host('site_discussion').getByRole('button',{name:'Отправить сообщение',exact:true}).click()
 pass('customer-chat-request-submitted-from-selected-section')
 await expect.poll(()=>report.job_id||'',{timeout:60000}).not.toBe('')
 await expect.poll(async()=>{
   const discussion=await api('query_discussion',scope)
   const response=discussion.messages.find(message=>message.id===report.job_id+'-response')
   if(response && !response.actions?.some(action=>action.label==='Посмотреть'))throw new Error('This request did not produce a proposal: '+response.text)
   return !!response
 },{timeout:240000,intervals:[2000,4000]}).toBe(true)
 await expect(host('site_discussion').getByRole('button',{name:'Посмотреть',exact:true}).last()).toBeVisible({timeout:240000})
 pass('real-model-produced-validated-proposal')
 await page.screenshot({path:output+'/live-model-proposal.png',fullPage:true})
 await host('site_discussion').getByRole('button',{name:'Посмотреть',exact:true}).last().click()
 await expect(host('chat_candidate_review')).toBeVisible()
 pass('candidate-preview-selected')
 await page.screenshot({path:output+'/live-candidate-preview.png',fullPage:true})
 const history=await api('query_site_history',scope); const change=history.change_sets.find(c=>c.change_set_id==='chat-'+report.job_id.slice(0,48)&&c.status==='proposed'); if(!change)throw new Error('No retained proposal for this user request');report.candidate=change.change_set_id;
 const candidate=await api('query_preview',{...scope,candidate_change_set_id:change.change_set_id}); const expected=structuredClone(before.source.resources);expected.find(r=>r.id==='section-hero').data.title='Create, test, evolve.';if(JSON.stringify(candidate.source.resources)!==JSON.stringify(expected))throw new Error('Unexpected candidate resource changes');
 await host('chat_candidate_review').locator('[data-command-id="apply"]').click();
 await expect(host('chat_candidate_review')).toBeHidden({timeout:60000});let after=await api('query_preview',scope);if(JSON.stringify(after.source.resources)!==JSON.stringify(expected))throw new Error('Draft did not match proposal');pass('owner-applied-exact-model-proposal');
 await page.reload({waitUntil:'domcontentloaded'}); await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({timeout:90000});after=await api('query_preview',scope);if(after.source.resources.find(r=>r.id==='section-hero').data.title!=='Create, test, evolve.')throw new Error('Edit lost after reopen');pass('model-edit-retained-after-reopen');
 await host('site_discussion').getByRole('button',{name:'Отменить правку',exact:true}).last().click();await expect.poll(async()=>JSON.stringify((await api('query_preview',scope)).source.resources),{timeout:60000}).toBe(JSON.stringify(before.source.resources));pass('owner-undid-model-edit-and-restored-original-content');
 await page.screenshot({path:output+'/live-restored-draft.png',fullPage:true})
 if (report.errors.length || report.calls.some(call=>call.status>=400)) throw new Error('Browser or tool request errors were observed')
 report.passed=true
}catch(error){report.passed=false;report.errors.push(String(error));await page.screenshot({path:output+'/probe-failed.png',fullPage:true}).catch(()=>{})}
finally{await fs.writeFile(output+'/chat-authoring.json',JSON.stringify(report,null,2));await browser.close()}
console.log(JSON.stringify(report))
if(!report.passed)process.exitCode=1
