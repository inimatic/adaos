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
try {
 await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:90000})
 await expect(host('site_preview').locator('[data-preview-status="ready"]')).toBeVisible({timeout:90000})
 const active=(await api('query_active_project')).item;scope={project_id:active.id,selection_revision:active.revision};
 if(active.id!==project)throw new Error('Wrong project')
 before=await api('query_preview',scope)
 await tool('materials')
 await host('materials_tools').locator('[data-command-id="sources"]').click()
 await host('source_files').getByRole('button',{name:'Загрузить исходный файл',exact:true}).click()
 const filename='beta-authoring-'+Date.now()+'.txt'
 const marker='Private original for Site Studio beta verification. Hero heading: Create, test, evolve.'
 const field=id=>host('source_editor').locator(`[data-webui-field-id="${id}"]`).locator('input,textarea,select').first()
 await field('title').fill(filename)
 await field('folder').fill('beta-qualification')
 await host('source_editor').locator('input[type="file"]').setInputFiles({name:filename,mimeType:'text/plain',buffer:Buffer.from(marker)})
 await expect(host('source_editor').locator('.file-list')).toContainText(filename,{timeout:60000})
 await host('source_editor').getByRole('button',{name:'Добавить',exact:true}).click()
 await expect(host('source_editor')).toBeHidden({timeout:60000})
 await expect(host('source_files').getByText(filename,{exact:true})).toBeVisible({timeout:60000})
 let file=(await api('query_source_files',scope)).items.find(item=>item.title===filename);if(!file)throw new Error('Source not retained')
 report.source_id=file.id
 const read=await api('read_source_material',{...scope,id:file.id,expected_revision:file.revision,expected_digest:file.digest})
 if(read.item.content!==marker)throw new Error('MCP source bytes mismatch')
 pass('real-private-file-upload-and-scoped-mcp-read')
 await host('source_files').getByText(filename,{exact:true}).click()
 await expect(field('content')).toHaveValue(marker)
 await field('note').fill('Verified private original')
 await host('source_editor').getByRole('button',{name:'Сохранить',exact:true}).click()
 await expect(host('source_editor')).toBeHidden({timeout:60000})
 file=(await api('query_source_files',scope)).items.find(item=>item.id===file.id)
 if(file.note!=='Verified private original'||file.revision!==2)throw new Error('Source metadata edit failed')
 pass('source-preview-and-versioned-metadata-edit')
 await tool('structure')
 await host('v_sections').getByText('Create an app.',{exact:true}).first().click()
 await tool('chat')
 const composer=host('site_discussion').locator('textarea').last()
 await composer.fill('@'+filename)
 await host('site_discussion').getByRole('option').filter({hasText:filename}).click()
 await expect(host('site_discussion').locator('.reference-chips')).toContainText(filename)
 await composer.fill('Прочитай @['+filename+']. Предложи заменить только заголовок выбранного главного экрана значением Hero heading из этого материала. Другие поля и описание проекта не меняй.')
 const sent=page.waitForResponse(response=>response.url().includes('/api/tools/call')&&String(response.request().postDataJSON()?.tool).endsWith(':discuss'))
 await host('site_discussion').getByRole('button',{name:'Отправить сообщение',exact:true}).click()
 const submitted=await (await sent).json();const run=(submitted.result??submitted).job_id;report.source_chat_job=run
 await expect.poll(async()=>{
   const response=(await api('query_discussion',scope)).messages.find(message=>message.id===run+'-response')
   if(response&&!response.actions?.some(action=>action.label==='Посмотреть'))throw new Error('Source request failed: '+response.text)
   return !!response
 },{timeout:240000,intervals:[2000,4000]}).toBe(true)
 const cid='chat-'+run.slice(0,48)
 const sourceCandidate=await api('query_preview',{...scope,candidate_change_set_id:cid})
 if(sourceCandidate.source.resources.find(resource=>resource.id==='section-hero').data.title!=='Create, test, evolve.')throw new Error('The material value was not used')
 await host('site_discussion').getByRole('button',{name:'Отклонить',exact:true}).last().click()
 await expect.poll(async()=>(await api('query_site_history',scope)).change_sets.find(change=>change.change_set_id===cid)?.status,{timeout:60000}).toBe('rejected')
 pass('real-model-read-selected-private-source-and-compiled-proposal')
 await composer.fill('')
 await tool('structure')
 await host('v_sections').getByText('Create an app.',{exact:true}).first().click()
 await tool('chat')
 await host('chat_context').locator('[data-command-id="properties"]').click()
 const editor=host('component_property_editor')
 const props=(await api('query_component_fields',{...scope,id:'hero'})).items[0]
 const titleField=Object.keys(props.field_paths).find(key=>props.field_paths[key]==='title')
 await editor.locator(`[data-webui-field-id="${titleField}"] input`).fill('Property verification')
 await editor.getByRole('button',{name:'Сохранить',exact:true}).click()
 await expect.poll(async()=>(await api('query_preview',scope)).source.resources.find(r=>r.id==='section-hero').data.title,{timeout:60000}).toBe('Property verification')
 pass('typed-component-form-saves-through-semantic-authoring')
 await page.locator('ion-modal:visible').getByRole('button',{name:'Close',exact:true}).click()
 let history=await api('query_site_history',scope);let last=history.change_sets.find(change=>change.latest_undo_available)
 if(!last)throw new Error('No latest Undo')
 await api('undo_site_change',{...scope,change_set_id:last.change_set_id,expected_source_digest:last.commit_receipt.source_digest})
 if(JSON.stringify((await api('query_preview',scope)).source.resources)!==JSON.stringify(before.source.resources))throw new Error('Property Undo did not restore exact resources')
 pass('property-change-undo-restores-original-content')
 const section=before.source.routes[0].sections.find(section=>section.component==='site.cardGrid')
 const resource=before.source.resources.find(resource=>resource.id===section.resource_ref)
 const item=resource.data.items[0]
 const textKey=['body','description','text','title'].find(key=>typeof item[key]==='string')
 if(!textKey)throw new Error('No editable collection item')
 await tool('structure');await host('v_sections').getByText(resource.data.title,{exact:true}).first().click();await tool('chat')
 await host('chat_context').locator('[data-command-id="properties"]').click()
 const nested=(await api('query_component_fields',{...scope,id:section.id,node_path:'items.0'})).items[0]
 await host('component_property_tree').getByText(item.title||item.name||item.label||'0',{exact:true}).first().click()
 const nestedField=Object.keys(nested.field_paths).find(key=>nested.field_paths[key]==='items.0.'+textKey)
 await expect(editor.locator(`[data-webui-field-id="${nestedField}"] input, [data-webui-field-id="${nestedField}"] textarea`)).toHaveValue(item[textKey])
 await editor.locator(`[data-webui-field-id="${nestedField}"] input, [data-webui-field-id="${nestedField}"] textarea`).fill(item[textKey]+' [Beta collection check]')
 await editor.getByRole('button',{name:'Сохранить',exact:true}).click()
 await expect.poll(async()=>(await api('query_preview',scope)).source.resources.find(value=>value.id===resource.id).data.items[0][textKey],{timeout:60000}).toBe(item[textKey]+' [Beta collection check]')
 await page.locator('ion-modal:visible').getByRole('button',{name:'Close',exact:true}).click()
 pass('nested-collection-item-edit-through-generated-form')
 await tool('changes')
 await expect(host('change_proposals').getByRole('button',{name:'Отменить',exact:true}).filter({visible:true}).first()).toBeVisible({timeout:60000})
 const undoButtons=host('change_proposals').getByRole('button',{name:'Отменить',exact:true});let undone=false
 for(let i=0;i<await undoButtons.count();i++)if(await undoButtons.nth(i).isVisible()&&await undoButtons.nth(i).isEnabled()){await undoButtons.nth(i).click();undone=true;break}
 if(!undone)throw new Error('No enabled history Undo command')
 await page.getByRole('button',{name:'Отменить правку',exact:true}).last().click()
 await expect.poll(async()=>JSON.stringify((await api('query_preview',scope)).source.resources),{timeout:60000}).toBe(JSON.stringify(before.source.resources))
 pass('history-undo-restores-exact-collection-content')
 await tool('materials')
 await host('materials_tools').locator('[data-command-id="sources"]').click()
 await host('source_files').getByText(filename,{exact:true}).click()
 await host('source_editor').getByRole('button',{name:'Удалить',exact:true}).click()
 await page.getByRole('button',{name:'Удалить',exact:true}).last().click()
 await expect(host('source_editor')).toBeHidden({timeout:60000})
 if((await api('query_source_files',scope)).items.some(item=>item.id===file.id))throw new Error('Source deletion not retained')
 pass('source-delete-through-confirmed-crud')
 await page.screenshot({path:output+'/live-source-properties.png',fullPage:true})
 if (report.errors.length || report.calls.some(call=>call.status>=400)) throw new Error('Browser or tool request errors were observed')
 report.passed=true
} catch(error) {
 report.passed=false;report.errors.push(String(error));await page.screenshot({path:output+'/features-failed.png',fullPage:true}).catch(()=>{})
} finally {
 await fs.writeFile(output+'/source-properties.json',JSON.stringify(report,null,2));await browser.close()
}
console.log(JSON.stringify(report))
if(!report.passed)process.exitCode=1
