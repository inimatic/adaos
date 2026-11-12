// Core-owned layered workbench acceptance. Providers and CPU matte are doubles;
// application handlers, SQLite, durable continuations and Client are real.
import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import crypto from 'node:crypto'

const expect = baseExpect.configure({timeout:30000}), env=process.env
const layout=env.ADAOS_E2E_LAYOUT, variant=env.ADAOS_E2E_RETOUCH_VARIANT||'exact'
if(!['wide','compact'].includes(layout)||!['exact','edited'].includes(variant)) throw Error('Explicit registered case required')
const root=path.resolve(env.ADAOS_E2E_RETOUCH_FIXTURE), output=path.resolve(env.ADAOS_E2E_OUTPUT)
if(!root.startsWith((await fs.realpath('.tmp'))+path.sep)||!output.startsWith((await fs.realpath('e2e/artifacts'))+path.sep)) throw Error('Owned test paths required')
const endpoint=JSON.parse(await fs.readFile(path.join(root,'fixture-endpoint.json'),'utf8'))
const marker=JSON.parse(await fs.readFile(path.join(root,'owned-fixture.json'),'utf8'))
if(!marker.test||marker.source_digest!==endpoint.source_digest) throw Error('Fixture ownership drift')
const secret=(await fs.readFile(endpoint.token_file,'utf8')).trim(), token=env.ADAOS_E2E_HUB_TOKEN
if(!token) throw Error('Local control token required')
const hash=v=>'sha256:'+crypto.createHash('sha256').update(v).digest('hex')
const fixture=async(p,options={})=>{
 const r=await fetch(endpoint.base_url+p,{...options,headers:{'X-Retouch-Fixture-Token':secret,...options.headers,connection:'close'}})
 if(!r.ok) throw Error(`Owned fixture ${p}: ${r.status}`)
 return r
}
const audit=async()=> (await fixture('/audit')).json()
const control=values=>fixture('/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)})
const obligations=['loading-error-empty','groups-import-lossless-thumbnails','native-frame-local-draft',
 'seven-operation-goal-snapshot','fast-ack-auto-progress','reopen-idempotence','raw-alpha-advisory-delivery',
 'compare-zoom-pan-difference-download','reassembly-noop-protection','series-variants-history',
 'final-export-adopt','cancel-no-next-submit']
const specification={schema:'adaos.e2e.retouch_journey_spec.v1',layout,variant,source_digest:endpoint.source_digest,
 runner_digest:hash(await fs.readFile(new URL(import.meta.url))),obligations,
 scope:'Real layered handlers, SQLite, durable queue and Client; synthetic matte/provider. No live quality or Core admission claim.'}
const report={schema:'adaos.e2e.retouch_journey.v1',specification,checks:[],errors:[],calls:[],downloads:[],passed:false}
await fs.mkdir(output,{recursive:true})
await fs.writeFile(path.join(output,'verification-spec.json'),JSON.stringify(specification,null,2))
const before=await audit();if(before.series.length||before.jobs.length)throw Error('Fresh fixture required')
const browser=await chromium.launch({headless:true})
const viewport=layout==='wide'?{width:1600,height:1000}:{width:390,height:844}
const context=await browser.newContext({viewport,acceptDownloads:true})
report.browser_environment={browser_version:browser.version(),viewport,node_version:process.version,platform:process.platform}
await context.tracing.start({screenshots:true,snapshots:true,sources:true})
const hub=env.ADAOS_E2E_HUB_URL||'http://127.0.0.1:8777',subnet=env.ADAOS_E2E_SUBNET_ID||'sn_6acf0c01'
const url=new URL(env.ADAOS_E2E_CLIENT_URL||'http://127.0.0.1:8100/')
for(const[k,v]of Object.entries({intent:'webspace.open',zone:'lo',subnet_id:subnet,webspace_id:env.ADAOS_E2E_WEBSPACE_ID||'desktop-dev',expected_scenario_id:'natural_retouch',try_local_hub:'1',scenario_id:'natural_retouch'}))url.searchParams.set(k,v)
const expected=JSON.parse(env.ADAOS_E2E_EXPECTED_MATERIALIZATION||'{}')
await context.addInitScript(({hub,token,subnet,origin})=>{
 if(location.origin!==origin)return
 window.__ADAOS_DEBUG__=true;window.__ADAOS_BASE__=hub;window.__ADAOS_TOKEN__=token
 for(const[k,v]of Object.entries({adaos_device_id:'owned-layered-fixture',adaos_hub_base:hub,adaos_local_hub_base:hub,adaos_try_local_hub:'1',adaos_hub_token:token,adaos_local_subnet_id:subnet,adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'ru'}))localStorage.setItem(k,v)
},{hub,token,subnet,origin:url.origin})
let readFailure=true,releaseRead;const firstRead=new Promise(resolve=>releaseRead=resolve)
await context.route('**/api/tools/**',async route=>{
 const request=route.request(),u=new URL(request.url());let body
 try{body=request.postDataJSON()}catch{}
 const owned=body?.tool?.startsWith('natural_retouch_skill:')||u.pathname.startsWith('/api/tools/natural_retouch_skill/')
 if(!owned){report.errors.push('Foreign tool refused');return route.abort()}
 if(body?.tool?.endsWith(':workspace_query')&&body.arguments?.filters?.kind==='photo'&&readFailure){
  await firstRead
  return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({ok:false,error:'synthetic_initial_read_failure'})})
 }
 try{
  const headers={...request.headers(),'X-Retouch-Fixture-Token':secret,connection:'close'};delete headers.authorization;delete headers.cookie;delete headers['x-adaos-token']
  // The owned Uvicorn fixture can close an idle keep-alive socket between
  // reads. Playwright retries ECONNRESET only, never an HTTP status. A write
  // has an unknown outcome after a reset and must remain single-attempt.
  const maxRetries=body?.tool==='natural_retouch_skill:workspace_query'?1:0
  const r=await route.fetch({url:endpoint.base_url+u.pathname+u.search,headers,timeout:60000,maxRetries})
  report.calls.push({tool:body?.tool,operation:body?.arguments?.operation,status:r.status(),runtime_source:r.headers()['x-adaos-runtime-source']})
  const bytes=await r.body()
  if(body?.tool){try{const value=JSON.parse(bytes.toString());if(value.ok===false||value.result?.ok===false){report.tool_errors||=[];report.tool_errors.push({tool:body.tool,result:value,revision:body.arguments?.revision})}}catch{}}
  await route.fulfill({response:r,body:bytes})
 }catch(e){report.errors.push(String(e.message).split('\n')[0].replaceAll(secret,'[redacted]'));await route.abort()}
})
let page=await context.newPage();const assets=[];report.client_assets=[]
function observe(p){
 p.on('pageerror',e=>report.errors.push(e.message))
 p.on('response',r=>{const u=new URL(r.url());if(u.origin===url.origin&&/\.(m?js|css)$/.test(u.pathname)&&r.ok())assets.push(r.body().then(b=>report.client_assets.push({path:u.pathname,digest:hash(b),size_bytes:b.length})).catch(()=>{}))})
}observe(page)
// Core admission is outside this provider-free fixture. A real request from
// another owner session can open the node-wide PA panel over our canvas.
// Dismiss its presentation only; never approve, decline or answer an action.
await page.addLocatorHandler(page.locator('ion-modal.pending-actions-overlay.show-modal'),async overlay=>{
 report.external_pending_panels_closed=(report.external_pending_panels_closed||0)+1
 await overlay.locator('.pending-actions-panel__header button').click()
 await expect(overlay).not.toBeVisible()
})
const host=id=>page.locator(`[data-webui-widget-id="${id}"]`).last()
const button=(widget,id)=>host(widget).locator(`[data-command-id="${id}"]`)
const field=(widget,id)=>host(widget).locator(`[data-webui-field-id="${id}"]`).locator('input,textarea,select').first()
// Narrow list columns render actions in a sibling row, even on a wide screen.
// Click the visible presentation instead of the hidden inline duplicate.
const itemCommand=(row,id)=>row.locator(`[data-command-id="${id}"]:visible`).or(
 row.locator('xpath=following-sibling::*[1][contains(@class,"list-row-actions-below")]')
  .locator(`[data-command-id="${id}"]:visible`)).first()
const viewer=()=>host('image_compare')
async function step(id,fn){report.active=id;const evidence=await fn();report.checks.push({id,passed:true,evidence});await fs.writeFile(path.join(output,'journey.json'),JSON.stringify(report,null,2));console.log(`${layout}: ${id} passed`)}
async function ready(){
 await page.waitForFunction(({expected})=>{const s=window.__ADAOS_DEBUG_STATE__?.()?.sync,v=s?.materialization||{};return s?.materializationReady&&v.currentScenario==='natural_retouch'&&Object.entries(expected).every(([k,x])=>({revision:v.materializationRevision,source_fingerprint:v.sourceFingerprint})[k]===x)},{expected},{timeout:90000})
 report.observed_materializations||=[];report.observed_materializations.push(await page.evaluate(()=>{const v=window.__ADAOS_DEBUG_STATE__().sync.materialization;return{revision:v.materializationRevision,source_fingerprint:v.sourceFingerprint}}))
 await expect(viewer()).toBeAttached()
}
async function reveal(id){
 if(await host(id).isVisible())return
 const panel=await host(id).evaluate(el=>el.closest('.workspace-panel')?.id)
 if(panel)await page.locator(`button[aria-controls="${panel}"]`).click()
 await expect(host(id)).toBeVisible()
}
async function canvas(){const close=page.locator('ada-workspace-layout .workspace-panel:not([hidden]) .panel-header button').first();if(await close.isVisible())await close.click()}
async function library(){await page.getByText('Фотографии',{exact:true}).first().click();await expect(host('photo_cards')).toBeVisible()}
async function closeModal(){const m=page.locator('ion-modal.show-modal').last();await m.getByText(/^(Close|Закрыть)$/i,{exact:true}).last().click();await expect(m).not.toBeVisible()}
async function reply(action,tool,operation){
 const r=page.waitForResponse(r=>{if(!r.url().includes('/api/tools/call'))return false;const b=r.request().postDataJSON();return b?.tool===`natural_retouch_skill:${tool}`&&(!operation||b.arguments?.operation===operation)},{timeout:60000})
 await action();const http=await r,b=await http.json(),v=b.result??b;expect(http.ok()&&b.ok!==false&&v.ok!==false,JSON.stringify(v).slice(0,1200)).toBe(true);return v
}
async function selectPhoto(){await library();await host('photo_cards').getByText('fixture.jpg',{exact:true}).click();await expect(host('photo_cards')).not.toBeVisible();await canvas();const image=viewer().locator('.stage img').first();await expect(image).toBeVisible();await expect.poll(()=>image.evaluate(i=>i.naturalWidth)).toBe(320)}
async function frame(){
 await canvas();await viewer().getByRole('button',{name:'Оригинал',exact:true}).click();await viewer().getByRole('button',{name:'Вписать',exact:true}).click()
 // Difference view hides masks. Select the real crop tool before deciding
 // whether a frame exists, otherwise a drag resizes its hidden NW handle.
 await page.getByText('Рамка',{exact:true}).click()
 await expect(viewer().locator('svg.annotation-layers')).toBeVisible()
 const existing=viewer().locator('svg.annotation-layers > rect[stroke="#ffd66d"]')
 if(await existing.count()){
  expect(Number(await existing.getAttribute('width'))).toBeGreaterThan(50);expect(Number(await existing.getAttribute('height'))).toBeGreaterThan(50);return
 }
 const image=viewer().locator('.stage img').first(),b=await image.boundingBox()
 await page.mouse.move(b.x+b.width*.05,b.y+b.height*.05);await page.mouse.down();await page.mouse.move(b.x+b.width*.95,b.y+b.height*.95,{steps:10});await page.mouse.up()
 await expect.poll(async()=>Number(await existing.getAttribute('width'))).toBeGreaterThan(50)
 await expect.poll(async()=>Number(await existing.getAttribute('height'))).toBeGreaterThan(50)
 await expect(button('sticky_commands','retouch')).toBeEnabled()
}
const goal=variant==='edited'?'Убрать тестовое пятно, мягко ослабить складки шеи; сохранить ткань.':'Мягкая ретушь кожи и складок шеи с сохранением ткани.'
let photo,series,result,nativeControl=false
async function start({variantsMode='one'}={}){
 await reveal('retouch_goal_form');await field('retouch_goal_form','goal').fill(goal);await field('retouch_goal_form','strength').fill('30')
 await button('sticky_commands','retouch').click();const m=page.locator('ion-modal.show-modal').last();await field('retouch_confirm_form','target_confirmed').check()
 if(variantsMode==='three')await field('retouch_confirm_form','variants_mode').selectOption({label:'Три независимых: 30 / 50 / 70'})
 const start=Date.now(),v=await reply(()=>host('retouch_confirm_form').getByRole('button',{name:'Подтвердить обработку',exact:true}).click(),'workspace_run')
 expect(Date.now()-start).toBeLessThan(5000);await expect(m).not.toBeVisible();return v.item
}
async function confirmSourceAlpha(id){
 // This is an explicit review of the known synthetic matte, never an approval
 // of user/provider data. Exercise the same visible controls an owner uses.
 if(page.url()==='about:blank'){
  await page.goto(url.href,{waitUntil:'domcontentloaded'});await ready();await selectPhoto()
 }
 await host('editor_stage_tabs').getByText('Оценка',{exact:true}).click();await reveal('series_history')
 const card=host('series_history').locator(`article.note-card:has([data-focus-ref$="${id}"]), ion-item[data-focus-ref$="${id}"]`)
 await expect(card).toHaveCount(1)
 await itemCommand(card,'review_a0').click()
 await canvas()
 await expect(button('native_editor_tools','edit')).toBeEnabled()
 await expect(button('native_editor_tools','erase')).toBeEnabled()
 await host('layer_selector').locator('.selector__control').click()
 await page.locator('.selector__option').getByText('Контур исходника',{exact:true}).click()
 await expect(host('layer_selector').locator('.selector__value')).toHaveText('Контур исходника')
 await expect(viewer().locator('svg.annotation-layers')).toBeVisible()
 const before=await audit(),paid=before.jobs.filter(j=>j.request_id.includes(id))
 await reveal('series_history')
 await expect(itemCommand(card,'confirm_a0')).toBeEnabled()
 const answer=await reply(()=>itemCommand(card,'confirm_a0').click(),'workspace_run')
 expect(answer.item.id).toBe(id)
 const after=await audit(),same=after.series.find(s=>s.id===id)
 expect(same.source_alpha_confirmed).toBe(true)
 for(const job of paid){
  const retained=after.jobs.find(j=>j.request_id===job.request_id)
  expect(retained?.submits).toBe(job.submits);expect(retained?.input_digest).toBe(job.input_digest)
 }
 report.source_alpha_reviews||=[]
 report.source_alpha_reviews.push({series_id:id,explicit_ui_review:true,original_native_alpha:true,
  retained_requests:paid.map(j=>j.request_id),same_series:true})
}
async function completed(id){
 const initial=(await audit()).series.find(s=>s.id===id),native=initial?.snapshot?.pipeline_version?.startsWith('openai-native-')
 const timeout=native?60000+150000*initial.snapshot.variants:90000
 let lastProgress=null,lastChanged=Date.now()
 await expect.poll(async()=>{
  const facts=await audit(),s=facts.series.find(s=>s.id===id)
  const progress=JSON.stringify([s?.status,s?.stage,s?.native_variant_index,s?.variants?.length,s?.active_job_request_id,s?.progress])
  if(progress!==lastProgress){lastProgress=progress;lastChanged=Date.now()}
  if(Date.now()-lastChanged>30000)throw Error('Owned native fixture made no semantic progress for30s: '+progress)
  if(s?.status==='completed')return true
  if(s?.status==='input_required'&&s.review_kind==='source_alpha'){
   await confirmSourceAlpha(id);lastChanged=Date.now();return false
  }
  if(!s?.snapshot?.pipeline_version?.startsWith('openai-native-')||s.status!=='input_required'||s.whole_photo_status!=='needs_review')return false
  const useful=s.variants?.filter(v=>v.raw&&v.alpha&&v.assembled&&v.qa_terminal)||[]
  return new Set(useful.map(v=>v.group)).size===s.snapshot.variants
 },{timeout}).toBe(true)
}
try{
 await step('loading-error-empty',async()=>{
  await page.goto(url.href,{waitUntil:'domcontentloaded'});await ready();await library();releaseRead();await expect(host('photo_cards').getByRole('button',{name:/Retry|Повтор/i})).toBeVisible();readFailure=false
  await page.reload();await ready();await library();await expect(host('photo_cards').getByRole('button',{name:/Retry|Повтор/i})).not.toBeVisible();expect((await audit()).photos).toHaveLength(0);expect(await host('photo_cards').locator('.collection-focus-item').count()).toBe(0);await closeModal()
  return{injected_read_failure:true,empty_observed:true}
 })
 await step('groups-import-lossless-thumbnails',async()=>{
  await library();await reveal('group_list');await expect(host('group_list').getByText('Группа 1',{exact:true})).toBeVisible();await canvas()
  await reveal('group_list');await host('group_list').getByText('Создать',{exact:true}).click()
  await field('group_create_form','title').fill('Контрольное мероприятие')
  await reply(()=>host('group_create_form').getByRole('button',{name:'Создать',exact:true}).click(),'workspace_command','group_create')
  let group=host('group_list').locator('.collection-focus-item').filter({hasText:'Контрольное мероприятие'}).first()
  await expect(group).toBeVisible();await itemCommand(group,'edit').click()
  await expect(field('group_edit_form','title')).toHaveValue('Контрольное мероприятие')
  await field('group_edit_form','title').fill('Проверенное мероприятие')
  await reply(()=>host('group_edit_form').getByRole('button',{name:'Сохранить',exact:true}).click(),'workspace_command','group_update')
  group=host('group_list').locator('.collection-focus-item').filter({hasText:'Проверенное мероприятие'}).first()
  await expect(group).toBeVisible();await itemCommand(group,'delete').click()
  await field('delete_group_form','confirmation').selectOption({label:'Все фото перенесены, удалить пустую группу'})
  await reply(()=>host('delete_group_form').getByRole('button',{name:'Удалить пустую группу',exact:true}).click(),'workspace_command','group_delete')
  expect((await audit()).groups).toHaveLength(1);await canvas()
  await page.getByText('Добавить фото',{exact:true}).last().click()
  const sample=Buffer.from(await(await fixture('/sample-image')).arrayBuffer())
  await host('add_photo_form').locator('input[type=file]').setInputFiles({name:'fixture.jpg',mimeType:'image/jpeg',buffer:sample})
  await field('add_photo_form','filename').fill('fixture.jpg')
  await field('add_photo_form','format').fill('JPEG');await field('add_photo_form','date').fill('2026-10-24');await field('add_photo_form','group_id').selectOption({label:'Группа 1'})
  await reply(()=>host('add_photo_form').getByRole('button',{name:/Добавить|Сохранить/}).last().click(),'workspace_command','import')
  await expect(host('photo_cards').getByText('fixture.jpg',{exact:true})).toBeVisible()
  const thumb=host('photo_cards').locator('img').first();await expect.poll(()=>thumb.evaluate(i=>i.naturalWidth)).toBeGreaterThan(0);expect(await thumb.evaluate(i=>i.naturalWidth)).toBeLessThanOrEqual(512)
  const facts=await audit();photo=facts.photos[0];expect(photo.group_id).toBe('group-1');await closeModal();await selectPhoto();return{photo_id:photo.id}
 })
 await step('native-frame-local-draft',async()=>{
  await page.getByRole('tab',{name:'Подготовка',exact:true}).click();await reveal('prepare_panel')
  await field('prepare_panel','tags').fill('контроль');await field('prepare_panel','tags').press('Enter')
  await reply(()=>host('prepare_panel').getByRole('button',{name:'Сохранить теги',exact:true}).click(),'workspace_command','photo_update')
  expect((await audit()).photos.find(p=>p.id===photo.id).tags).toEqual(['контроль'])
  await page.getByRole('tab',{name:'Ретушь',exact:true}).click();await frame();expect((await audit()).jobs).toHaveLength(0)
  return{native:[320,240],tags_saved:true,no_provider_work:true}
 })
 await step('seven-operation-goal-snapshot',async()=>{
  await page.getByRole('tab',{name:'Ретушь',exact:true}).click();await reveal('retouch_goal_form');await field('retouch_goal_form','goal').fill(goal)
  await page.getByText('Семь операций и требования…',{exact:true}).click();expect(await host('all_operations').locator('input[type=checkbox]').count()).toBe(7)
  await field('all_operations','hair_enabled').check();await field('all_operations','hair_prompt').fill('Уложить одиночные волосы, сохраняя прическу.');await closeModal()
  expect((await audit()).jobs).toHaveLength(0);return{goal,separate_operation_prompt:true}
 })
 await step('fast-ack-auto-progress',async()=>{
  await control({hold_phase:'edit'});series=await start();expect(series.status).toBe('queued')
  await expect.poll(async()=>(await audit()).jobs.length,{timeout:30000}).toBe(1)
  nativeControl=(await audit()).series.find(s=>s.id===series.id)?.snapshot?.pipeline_version?.startsWith('openai-native-')===true
  // Manual review deliberately skips QA. Fail the next retouch instead, so
  // extraction is retained and the same recovery/idempotence path is exercised.
  if(nativeControl)await control({quota_phase:'retouch'})
  return{series_id:series.id}
 })
 await step('reopen-idempotence',async()=>{
  const before=await audit();await page.goto('about:blank');await control({hold_phase:null})
  if(nativeControl){
   await expect.poll(async()=>{
    const s=(await audit()).series.find(s=>s.id===series.id)
    return s?.status==='input_required'&&(s.provider_status==='failed'||s.review_kind==='source_alpha')
   },{timeout:90000}).toBe(true)
    if((await audit()).series.find(s=>s.id===series.id)?.review_kind==='source_alpha'){
     await confirmSourceAlpha(series.id)
     await page.goto('about:blank')
     await expect.poll(async()=>{
      const s=(await audit()).series.find(s=>s.id===series.id)
      return s?.status==='input_required'&&s.provider_status==='failed'
     },{timeout:90000}).toBe(true)
    }
   const stopped=await audit(),saved=stopped.series.find(s=>s.id===series.id)
   const paid=stopped.jobs.filter(j=>j.kind==='edit'&&j.response_status==='completed')
   expect(paid.length).toBeGreaterThan(0)
   const failed=saved.active_job_request_id;expect(stopped.jobs.find(j=>j.request_id===failed)?.response_status).toBe('failed')
   await page.goto(url.href,{waitUntil:'domcontentloaded'});await ready();await selectPhoto()
   await host('editor_stage_tabs').getByText('Оценка',{exact:true}).click();await reveal('series_history')
   const card=host('series_history').locator(`article.note-card:has([data-focus-ref$="${series.id}"]), ion-item[data-focus-ref$="${series.id}"]`)
   await expect(card).toHaveCount(1)
   const resume=card.locator('[data-command-id="retry_provider"]');await expect(resume).toBeVisible()
   await control({quota_phase:null})
   const retryRequest=page.waitForRequest(r=>{try{const b=r.postDataJSON();return b?.tool==='natural_retouch_skill:workspace_run'&&b.arguments?.options?.resume_mode==='retry_provider'}catch{return false}})
   const resumed=await reply(()=>resume.click(),'workspace_run');expect(resumed.item.id).toBe(series.id)
   expect((await retryRequest).postDataJSON().arguments.editor).toEqual({})
   await page.goto('about:blank');await completed(series.id)
   const recovered=await audit(),same=recovered.series.find(s=>s.id===series.id)
   expect(recovered.series).toHaveLength(1);expect(same.snapshot_sha256).toBe(saved.snapshot_sha256)
   const retries=same.provider_retries.filter(r=>r.parent_request_id===failed)
   expect(retries).toHaveLength(1);expect(retries[0].request_id).not.toBe(failed)
   expect(recovered.jobs.find(j=>j.request_id===failed)?.response_status).toBe('failed')
   expect(recovered.jobs.find(j=>j.request_id===retries[0].request_id)?.response_status).toBe('completed')
   for(const job of paid){const retained=recovered.jobs.find(j=>j.request_id===job.request_id);expect(retained?.submits).toBe(1);expect(retained?.input_digest).toBe(job.input_digest)}
   report.quota_recovery={series_id:series.id,failed_request_id:failed,retry_request_id:retries[0].request_id,retained_successful_requests:paid.map(j=>j.request_id),snapshot_unchanged:true}
  }else await completed(series.id)
  await page.goto(url.href,{waitUntil:'domcontentloaded'});await ready();await selectPhoto()
  const after=await audit(),s=after.series.find(s=>s.id===series.id);expect(s.snapshot.goal).toBe(goal);expect(Object.keys(s.snapshot.operations)).toHaveLength(7);expect(s.snapshot.operations.hair.prompt).toContain('одиночные');expect(after.jobs.every(j=>j.submits===1)).toBe(true)
  await page.getByRole('tab',{name:'Ретушь',exact:true}).click();await reveal('retouch_goal_form');await expect(field('retouch_goal_form','strength')).toHaveValue('30');await expect(field('retouch_goal_form','goal')).toHaveValue(goal)
  await page.getByText('Семь операций и требования…',{exact:true}).click();await expect(field('all_operations','hair_enabled')).toBeChecked();await expect(field('all_operations','hair_prompt')).toHaveValue('Уложить одиночные волосы, сохраняя прическу.');await closeModal()
  expect(before.series).toHaveLength(1);return{background_completion:true,same_series_id:series.id}
 })
 await step('raw-alpha-advisory-delivery',async()=>{
  const s=(await audit()).series.find(s=>s.id===series.id);result=s.variants[0];expect(result.raw&&result.alpha&&result.assembled).toBeTruthy()
  if(s.snapshot.pipeline_version?.startsWith('openai-native-')){
   if(s.snapshot.review_policy==='manual'){
    expect(result.qa.complete).toBe(false);expect(result.qa.qa_performed).toBe(false)
    expect((await audit()).jobs.filter(j=>j.kind==='qa'&&j.request_id.includes(s.id))).toHaveLength(0)
   }else expect(result.qa.complete).toBe(true)
   expect(result.qa.uncertain).toBe(true);expect(s.whole_photo_status).toBe('needs_review')
  }else expect(result.qa.data.verdict).toBe('requires_review')
  await expect.poll(()=>viewer().locator('.stage img').count()).toBe(2);return{assembled:result.assembled,raw:result.raw,alpha:result.alpha,qa_advisory:true}
 })
 await step('compare-zoom-pan-difference-download',async()=>{
  await viewer().getByRole('button',{name:'100%',exact:true}).click();await viewer().getByRole('button',{name:'Показать изменения',exact:true}).click()
  const stages=viewer().locator('.stage'),worlds=stages.locator('.world'),stage=stages.first()
  await stage.scrollIntoViewIfNeeded()
  expect(await stage.evaluate(el=>{const r=el.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('.stage')===el})).toBe(true)
  const before=await worlds.evaluateAll(els=>els.map(e=>getComputedStyle(e).transform)),b=await stage.boundingBox(),x=b.x+b.width/2,y=b.y+b.height/2
  await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+15,y+10);await page.mouse.up()
  const transforms=await worlds.evaluateAll(els=>els.map(e=>getComputedStyle(e).transform));expect(transforms).toHaveLength(2);expect(new Set(transforms).size).toBe(1);expect(transforms).not.toEqual(before)
  return{zoom_pan_synchronous:true}
 })
 await step('reassembly-noop-protection',async()=>{
  await page.getByRole('tab',{name:'Сборка',exact:true}).click();await reveal('assembly_commands');const n=(await audit()).jobs.length
  const v=await reply(()=>button('assembly_commands','assemble').click(),'workspace_command','reassemble');expect(v.artifact_id).toBe(result.assembled);expect((await audit()).jobs).toHaveLength(n);return{artifact_id:v.artifact_id,no_model_calls:true}
 })
 await step('series-variants-history',async()=>{
  const errorsBefore=report.tool_errors?.length||0
  await page.getByRole('tab',{name:'Ретушь',exact:true}).click();const independent=await start({variantsMode:'three'});await completed(independent.id)
  const facts=await audit(),three=facts.series.find(s=>s.id===independent.id)
  expect(three.variants.map(v=>v.level)).toEqual([30,50,70]);expect(three.snapshot.source_sha256).toBe(facts.series.find(s=>s.id===series.id).snapshot.source_sha256)
  expect(three.variants.every(v=>v.raw&&v.alpha&&v.assembled)).toBe(true);expect(facts.jobs.every(j=>j.submits===1)).toBe(true)
  await expect.poll(()=>viewer().locator('.stage img:not(.difference-map)').count()).toBe(2)
  expect((report.tool_errors||[]).slice(errorsBefore).filter(e=>JSON.stringify(e).includes('artifact_ownership_mismatch'))).toEqual([])
  await host('editor_stage_tabs').getByText('Оценка',{exact:true}).click();await reveal('series_history')
  // Custom titles may repeat. Exercise the actual row for the immutable series
  // identity, rather than assuming the application discarded the custom title.
  await host('series_history').locator(`[data-focus-ref$="${series.id}"]`).click()
  await expect(host('history_results')).toBeVisible();return{selected_series:series.id,independent_series:independent.id,levels:[30,50,70]}
 })
 await step('final-export-adopt',async()=>{
  await reveal('history_results');const card=host('history_results').locator('article.note-card').filter({has:page.locator(`[data-focus-ref$="${result.assembled}"]`)}).first()
  await card.getByText('Выбрать финалом…',{exact:true}).click();await reply(()=>button('final_actions','confirm').click(),'workspace_command','final')
  expect((await audit()).photos.find(p=>p.id===photo.id).final_artifact_id).toBe(result.assembled)
  for(const[format,label]of [['PNG','Скачать PNG'],['JPEG','Скачать JPEG']]){
   const download=page.waitForEvent('download');await card.getByText(label,{exact:true}).click();const d=await download;const target=path.join(output,'assembled.'+(format==='PNG'?'png':'jpg'));await d.saveAs(target);report.downloads.push({kind:'assembled',format,path:path.basename(target),artifact_id:result.assembled})
  }
  await reply(()=>card.getByText('Сохранить как новый исходник',{exact:true}).click(),'workspace_command','adopt');const facts=await audit();expect(facts.photos).toHaveLength(2);expect(facts.photos.some(p=>p.parent_artifact_id===result.assembled&&p.title.includes('(1)'))).toBe(true);return{final_artifact_id:result.assembled}
 })
 await step('cancel-no-next-submit',async()=>{
  await selectPhoto();await page.getByRole('tab',{name:'Ретушь',exact:true}).click();await frame();await control({hold_phase:'edit'});const second=await start();expect(second.id).not.toBe(series.id)
  await expect.poll(async()=>(await audit()).jobs.filter(j=>j.request_id.includes(second.id)).length,{timeout:30000}).toBe(1)
  const n=(await audit()).jobs.length
  const cancelled=await reply(()=>host('sticky_auto_task').getByRole('button',{name:/Отмен|Cancel/}).click(),'workspace_command','cancel')
  expect(cancelled.item.id).toBe(second.id);expect(cancelled.item.cancel_requested).toBe(true)
  const native=cancelled.item.snapshot?.pipeline_version?.startsWith('openai-native-')
  expect(cancelled.item.status).toBe(native?'cancelled':'cancelling');await control({hold_phase:null})
  await expect.poll(async()=>(await audit()).series.find(s=>s.id===second.id)?.status,{timeout:30000}).toBe('cancelled');const final=await audit();expect(final.jobs).toHaveLength(n);expect(final.jobs.every(j=>j.submits===1)).toBe(true);return{cancelled_series_id:second.id,no_repair_after_cancel:true}
 })
 report.passed=report.checks.length===obligations.length&&!report.errors.length
}catch(e){report.failure=String(e.message).replaceAll(secret,'[redacted]').replaceAll(token,'[redacted]');report.failure_kind=/expect\(/.test(report.failure)?'candidate_postcondition':'runner_or_environment';console.log(`${layout}: ${report.active} FAILED: ${report.failure.slice(0,700)}`)}
finally{
 releaseRead();report.text=await page.locator('body').innerText().catch(()=>'');report.final_audit=await audit();await Promise.allSettled(assets)
 report.runtime=await page.evaluate(()=>window.__ADAOS_DEBUG_STATE__?.()).catch(()=>null);await page.screenshot({path:path.join(output,'final.png'),fullPage:true}).catch(()=>{})
 await context.tracing.stop({path:path.join(output,'private-trace.zip')});await fs.writeFile(path.join(output,'journey.json'),JSON.stringify(report,null,2));await browser.close()
}
console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,active:report.active,failure:report.failure}))
if(!report.passed)process.exitCode=1
