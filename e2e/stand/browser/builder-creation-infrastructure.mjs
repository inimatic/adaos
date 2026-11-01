import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { prototypeRowById, prototypeRowCommand, prototypeRowSelector, installSystemOverlayDismissal, dismissSystemOverlays, recordEditorOpener, selectPrototypeRowForCommand, revealPrototypeWidget } from './prototype-navigation.mjs'
import fs from 'node:fs/promises'
import path from 'node:path'

// This visible development oracle is never included in the LLM request.
const input=JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CREATION_INPUT,'utf8'))
const project=input.project
assert(/^test_creation_[a-f0-9]{18}$/.test(project),'Only this run-owned creation fixture may be operated')
const expectedRuCpu=92,verifyRepeat=input.repeat===true
const output=process.env.ADAOS_E2E_OUTPUT
await fs.mkdir(output,{recursive:true})
const accepted=input.accepted
assert.equal(accepted.project,project)
const source=accepted.webui
assert.equal(source.generated_by,'builder.semantic_compiler.v2')
const modals=source.ui.application.modals||{}
const widgets=[...source.ui.application.desktop.pageSchema.widgets,...Object.values(modals).flatMap(m=>m.schema?.widgets||[])]
const semantic=accepted.semantic_document
const sectionTitle=view=>view.section?.title?.ru||view.section?.label?.ru
const bySection=title=>semantic.views.find(v=>v.role==='collection'&&sectionTitle(v)===title)
const overview=bySection('Обзор'),incidents=bySection('Инциденты'),backups=bySection('Копии')
assert(overview&&incidents&&backups)
const command=semantic.commands.find(c=>c.label?.ru==='Принять')
assert(command)
const aliases={overview:overview.id,incident_list:incidents.id,backup_list:backups.id,
 incident_action:command.view_ref,'queries-overview':'queries-'+overview.id,
 'prototype-sections':widgets.find(w=>w.inputs?.variant==='tabs')?.id}
const actualId=id=>aliases[id]||id
const fieldId=(resource,field)=>{
 const fields=semantic.resources.find(r=>r.id===resource).fields
 const result=fields.find(f=>f.id.split('.').at(-1).toLowerCase()===field.toLowerCase()||f.label?.en===field||f.label?.ru===field)
 assert(result,resource+'/'+field);return result.id
}
const labelFor=(resource,field,value)=>{
 const options=semantic.resources.find(r=>r.id===resource).fields.find(f=>f.id===fieldId(resource,field)).options||[]
 return options.find(o=>o.value===value)?.label?.ru||value
}
const widget=id=>{const found=widgets.find(row=>row.id===actualId(id));assert(found,id);return found}
const browser=await chromium.launch({headless:true})
const report={ok:false,project,publicationRef:accepted.publication_ref,providerCalls:0,samples:[],operations:[],errors:[]}
try{
 for(const [layout,width,height] of [['wide',1440,1000],['compact',390,844]]){
  const sample={layout,width,checks:[],errors:[],queryResponses:[]};report.samples.push(sample)
  const context=await browser.newContext({viewport:{width,height},locale:'ru-RU'})
  await context.addInitScript(({token,hub,subnet})=>{
   for(const[k,v]of Object.entries({adaos_local_hub_url:hub,adaos_local_hub_token:token,
    adaos_local_subnet_id:subnet,adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'ru'}))localStorage.setItem(k,v)
  },{token:process.env.ADAOS_E2E_HUB_TOKEN,hub:process.env.ADAOS_E2E_HUB_URL,subnet:process.env.ADAOS_E2E_SUBNET_ID})
  const page=await context.newPage();page.setDefaultTimeout(20000)
  page.on('pageerror',error=>sample.errors.push(String(error)))
  page.on('request',request=>{
   if(new URL(request.url()).pathname!=='/api/resources/operate')return
   const value=request.postDataJSON()
   report.operations.push({resource_type:value.resource_type,operation:value.operation,record_id:value.record_id,payload:value.payload})
   if(!String(value.resource_type).startsWith('prototype.project.'+project+'.'))sample.errors.push('Mutation escaped owned Prototype')
  })
  page.on('response',async response=>{
   if(new URL(response.url()).pathname!=='/api/resources/query')return
   const request=response.request().postDataJSON()
   if(!String(request?.resource_type||'').startsWith('prototype.project.'+project+'.'))return
   const value=await response.json().catch(()=>null)
   sample.queryResponses.push({resource_type:request.resource_type,query:request.query||{search:request.search,search_fields:request.search_fields},status:response.status(),result:value})
  })
  const host=id=>page.locator(`[data-webui-widget-id=${JSON.stringify(actualId(id))}]`).filter({visible:true}).last()
  const rows=id=>host(id).locator(prototypeRowSelector(widget(id)))
  const dismiss=async()=>{
   await dismissSystemOverlays(page)
   const close=page.locator('.pending-actions-panel__header button').filter({visible:true}).last()
   if(await close.isVisible())await close.dispatchEvent('click')
  }
  const check=async(name,run)=>{
   try{await run();sample.checks.push({name,ok:true});console.log(JSON.stringify({layout,name,ok:true}))}
   catch(error){sample.checks.push({name,ok:false,error:String(error)});await dismiss();await page.screenshot({path:path.join(output,`${layout}-${name}-failure.png`),fullPage:true}).catch(()=>{})}
  }
  const assertRuAssociation=async(view,recordId,row)=>{
   const records=sample.queryResponses.filter(r=>r.resource_type.endsWith('.'+view.resource_ref))
    .map(r=>r.result?.items||[]).filter(items=>items.some(item=>String(item.id)===recordId)).at(-1)
   const actual=records?.find(item=>String(item.id)===recordId)
   assert(actual,`No UI query record for ${recordId}`)
   const servers=sample.queryResponses.filter(r=>r.resource_type.endsWith('.'+overview.resource_ref))
    .map(r=>r.result?.items||[]).find(items=>items.length===3)
   const target=servers?.find(item=>item[fieldId(overview.resource_ref,'name')]==='RU')
   assert(target,'No source-checked RU server in UI query evidence')
   const edges=semantic.relationships.filter(edge=>
    edge.from_resource_ref===view.resource_ref&&edge.to_resource_ref===overview.resource_ref||
    edge.to_resource_ref===view.resource_ref&&edge.from_resource_ref===overview.resource_ref)
   assert.equal(edges.length,1,'The owner projection must resolve one declared relationship')
   const edge=edges[0],forward=edge.from_resource_ref===view.resource_ref
   const local=forward?edge.from_field_ref:edge.to_field_ref,remote=forward?edge.to_field_ref:edge.from_field_ref
   assert(view.field_refs.includes(local),'The record owner must be displayed, not only stored')
   assert.notEqual(actual[local],null);assert.equal(actual[local],target[remote],`${recordId} must belong to RU, not merely to any existing server`)
   assert(await row.getByText('RU',{exact:true}).first().isVisible(),'No exact visible RU owner in the record row')
  }
  const tab=async id=>{
   const view={overview_tab:overview,incidents_tab:incidents,backups_tab:backups}[id]
   await revealPrototypeWidget(page,widgets,widget(view.id))
  }
  try{
   await installSystemOverlayDismissal(page)
   await page.goto(`${process.env.ADAOS_E2E_CLIENT_URL}?intent=webspace.open&zone=lo&subnet_id=${process.env.ADAOS_E2E_SUBNET_ID}&webspace_id=${input.preview_webspace_id}&scenario_id=${project}&expected_scenario_id=${project}&try_local_hub=1`,{waitUntil:'domcontentloaded',timeout:60000})
   await page.waitForFunction(ids=>ids.some(id=>[...document.querySelectorAll(`[data-webui-widget-id="${id}"]`)].some(el=>el.getClientRects().length)),[overview.id,incidents.id,backups.id],{timeout:60000});await dismiss()
   await check('alpha',async()=>{
    const meta=await page.evaluate(()=>{const c=window.ng.getComponent(document.querySelector('app-root'));return c.ydoc.toJSON(c.ydoc.getPath('ui/application/desktop/pageSchema'))._adaos})
    assert.equal(meta.materialization.stage,'prototype');assert.equal(meta.materialization.revision,'001')
    assert.equal(meta.releaseStage,'ALPHA');sample.materialization=meta.materialization
   })
   await tab('overview_tab')
   const assertServers=async()=>{
    await rows('overview').first().waitFor();assert.equal(await rows('overview').count(),3)
    const result=sample.queryResponses.filter(r=>r.resource_type.endsWith('.'+overview.resource_ref)&&r.result?.items?.length===3).at(-1)?.result
    assert(result,'No UI query receipt with all servers')
    for(const[name,cpu,disk,health]of[['EU',32,41,'healthy'],['RU',expectedRuCpu,78,'unhealthy'],['infra-01',21,36,'healthy']]){
     const matches=result.items.filter(r=>r[fieldId(overview.resource_ref,'name')]===name)
     assert.equal(matches.length,1,name);const row=matches[0]
     assert.deepEqual([row[fieldId(overview.resource_ref,'CPU')],row[fieldId(overview.resource_ref,'disk')],row[fieldId(overview.resource_ref,'health')]],[cpu,disk,health])
     const rendered=await prototypeRowById(host('overview'),widget('overview'),String(row.id)),text=await rendered.innerText()
     assert(text.includes(name)&&text.includes(String(cpu))&&text.includes(String(disk))&&text.includes(labelFor(overview.resource_ref,'health',health)),text)
    }
   }
   await check('source-values',assertServers)
   await check('demo-disclosure',async()=>{
    for(const view of widgets) {
     const el=host(view.id)
     if(await el.isVisible()&&/Демонстрационные данные/i.test(await el.innerText()))return
    }
    assert.fail('No visible demonstration disclosure in the owned Prototype')
   })
   await check('search-reset-and-field-scope',async()=>{
    const toolbar=host('queries-overview'),search=toolbar.locator('input[type=search]')
    try{
     for(const [query,count] of [['RU',1],['unhealthy',0],['name',0]]){
      await search.fill(query)
      await page.waitForFunction(({id,count,selector})=>{
       const el=[...document.querySelectorAll(`[data-webui-widget-id="${id}"]`)].find(e=>e.getClientRects().length)
       return el&&el.querySelectorAll(selector).length===count
      },{id:overview.id,count,selector:prototypeRowSelector(widget('overview'))})
      assert.equal(await rows('overview').count(),count)
      if(query==='RU')assert.match(await rows('overview').first().innerText(),/\bRU\b/)
     }
    }finally{await toolbar.locator('.query-toolbar__reset').click();await rows('overview').first().waitFor()}
    await assertServers()
   })
   await dismiss();await page.screenshot({path:path.join(output,`${layout}-overview.png`),fullPage:true})
   await tab('incidents_tab')
   await check('local-transition',async()=>{
    const row=await prototypeRowById(host('incident_list'),widget('incident_list'),'RU-I1')
    assert.equal(await rows('incident_list').count(),1)
    const before=sample.queryResponses.filter(r=>r.resource_type.endsWith('.'+incidents.resource_ref)).at(-1)?.result?.items
    sample.incidentBefore=before
    await assertRuAssociation(incidents,'RU-I1',row)
    if(!verifyRepeat&&layout==='wide')assert.equal(before?.[0]?.[fieldId(incidents.resource_ref,'status')],'open')
    await selectPrototypeRowForCommand(page,widgets,widget('incident_list'),row)
    const editor=widget('incident_action')
    const modalId=Object.entries(modals).find(([,m])=>(m.schema?.widgets||[]).some(w=>w.id===editor.id))?.[0]
    const opener=recordEditorOpener(widgets,modalId,editor.inputs?.selectedStateKey)
    if(opener) {
     if(opener.collection)await prototypeRowCommand(row,opener.commandId).click()
     else await host(opener.owner.id).locator(`[data-command-id=${JSON.stringify(opener.commandId)}]`).click()
    } else await revealPrototypeWidget(page,widgets,editor)
    const form=host('incident_action');await form.waitFor()
    const submit=form.locator(`button[data-command-id=${JSON.stringify(command.id)}],[data-command-id=${JSON.stringify(command.id)}] button`).first()
    if(before?.[0]?.[fieldId(incidents.resource_ref,'status')]==='accepted' && await submit.isDisabled()) {
     sample.repeatDisposition='disabled_after_accepted'
     assert.equal(await rows('incident_list').count(),1)
     const modal=page.locator('ion-modal').filter({visible:true}).last()
     if(await modal.isVisible()) {
      await modal.getByRole('button',{name:'Close',exact:true}).click()
      await modal.waitFor({state:'hidden'})
     }
     return
    }
    await submit.click()
    const alert=page.locator('ion-alert').filter({visible:true}).last()
    if(await alert.isVisible()){assert.match(await alert.innerText(),/Принять|accepted|измен/i);await alert.locator('button').last().click()}
    await page.waitForFunction(({label,id})=>[...document.querySelectorAll(`[data-webui-widget-id="${id}"]`)].some(e=>e.getClientRects().length&&e.innerText.includes(label)),{label:labelFor(incidents.resource_ref,'status','accepted'),id:incidents.id})
    assert.equal(await rows('incident_list').count(),1)
    assert.match(await row.innerText(),/Высокая загрузка CPU/)
    await assertRuAssociation(incidents,'RU-I1',row)
   })
   await dismiss();await page.screenshot({path:path.join(output,`${layout}-incidents.png`),fullPage:true})
   await tab('overview_tab');await check('preserved-server-values',assertServers)
   await tab('backups_tab')
   await check('backup-creation-and-root-version',async()=>{
    const row=await prototypeRowById(host('backup_list'),widget('backup_list'),'RU-backup')
    assert.equal(await rows('backup_list').count(),1)
    const text=await row.innerText();assert(text.includes('2026-10-29')&&text.includes('v0.1.0'),text)
    await assertRuAssociation(backups,'RU-backup',row)
    assert(await host('backup_list').getByText(/^(?:Дата создания|Создано|Создана|Создан)\s*:?\s*$/i).first().isVisible(),
     'No visible creation-date label; a timestamp value or a technical created_at ID is insufficient')
    assert(!/Дата проверки/i.test(await host('backup_list').innerText()),'A check-date label cannot stand for the source-owned creation date')
   })
   await check('no-page-overflow',async()=>{
    sample.dimensions=await page.evaluate(()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth}))
    assert(sample.dimensions.scroll<=width+2,JSON.stringify(sample.dimensions))
   })
   await dismiss();await page.screenshot({path:path.join(output,`${layout}-backups.png`),fullPage:true})
  }catch(error){sample.errors.push(String(error));await page.screenshot({path:path.join(output,`${layout}-fatal.png`)}).catch(()=>{})}
  await context.close()
 }
 report.ok=report.errors.length===0&&report.samples.every(s=>!s.errors.length&&s.checks.length===8&&s.checks.every(c=>c.ok))
}catch(error){report.errors.push(String(error))}
finally{await fs.writeFile(path.join(output,'functional-browser.json'),JSON.stringify(report,null,2),{flag:'wx'});await browser.close()}
console.log(JSON.stringify({ok:report.ok,project,samples:report.samples.map(({layout,checks,errors})=>({layout,checks,errors})),operations:report.operations,errors:report.errors}))
if(!report.ok)process.exitCode=1
