import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { sourceScalarTextMatches } from './prototype-source-scalar.mjs'
import { sourceCollectionOwner } from './prototype-source-owner.mjs'
import { prototypeRowById, prototypeFieldCell, prototypeFieldCaption, prototypeRowSelector,
  revealPrototypeWidget, installSystemOverlayDismissal, dismissSystemOverlays,
  selectPrototypeRow, selectPrototypeRowForCommand, recordEditorOpener, prototypeRowCommand } from './prototype-navigation.mjs'

const input=JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CREATION_INPUT,'utf8'))
const {project,accepted,records,checks,revision}=input
assert(/^test_creation_[a-f0-9]{18}$/.test(project))
const semantic=accepted.semantic_document
const webui=accepted.webui.ui.application
const widgets=[...webui.desktop.pageSchema.widgets,...Object.values(webui.modals||{}).flatMap(m=>m.schema?.widgets||[])]
const output=process.env.ADAOS_E2E_OUTPUT
await fs.mkdir(output,{recursive:true})
const report={ok:false,project,revision,level:input.level,samples:[],operations:[],errors:[]}
function locate(subject,sourceField){
 const facts=input.task_contract.facts.filter(f=>f.subject===subject&&f.source_field===sourceField)
 assert.equal(facts.length,1,`Missing/ambiguous retained source ${subject}.${sourceField}`)
 const binding=semantic.requirement_bindings.find(b=>b.requirement_ref===facts[0].assertion_ref)
 assert.equal(binding?.semantic_refs.length,1)
 const fieldId=binding.semantic_refs[0].replace(/^field:/,'')
 const resources=semantic.resources.filter(r=>r.fields.some(f=>f.id===fieldId))
 assert.equal(resources.length,1)
 const resource=resources[0],field=resource.fields.find(f=>f.id===fieldId)
 const rows=resource.records.filter(r=>r.id===subject||Object.values(r).includes(subject))
 assert.equal(rows.length,1)
 return {fact:facts[0],fieldId,field,resource,record:rows[0]}
}
function runtimeExpected(source,fixtureValue){
 const prior=input.runtime_before?.find(r=>r.resource_ref===source.resource.id)?.records.find(r=>r.id===source.record.id)
 return prior&&Object.hasOwn(prior,source.fieldId)?prior[source.fieldId]:fixtureValue
}
function collectionFor(source){
 const titleConstraints=input.task_contract.constraints.filter(c=>c.kind==='view_title'&&c.values.includes('Repair queue'))
 const ownerRefs=[...new Set(titleConstraints.map(c=>c.subject).filter(ref=>ref.startsWith('view:')))]
 // This independent fixture's original records belong to the retained queue.
 // Its typed source owner remains authoritative after additional projections.
 if(source.resource.id===locate('w1','title').resource.id&&ownerRefs.length){
  return sourceCollectionOwner(semantic.views,{resourceRef:source.resource.id,fieldRef:source.fieldId,ownerRefs})
 }
 const candidates=semantic.views.filter(v=>v.role==='collection'&&v.resource_ref===source.resource.id&&v.field_refs.includes(source.fieldId))
 if(candidates.length===1)return candidates[0]
 const constraints=input.task_contract.constraints.filter(c=>c.kind==='view_title'&&c.values.includes('Repair queue'))
 const refs=new Set(semantic.requirement_bindings.filter(b=>constraints.some(c=>c.assertion_ref===b.requirement_ref))
  .flatMap(b=>b.semantic_refs).filter(r=>r.startsWith('view:')).map(r=>r.slice(5)))
 const retained=candidates.filter(view=>refs.has(view.id))
 assert.equal(retained.length,1,'Locate the retained source-owned collection, not the first of several resource projections')
 return retained[0]
}
const browser=await chromium.launch({headless:true})
try{
 for(const[layout,width,height]of[['wide',1440,1000],['compact',390,844]]){
  const sample={layout,checks:[],errors:[],queries:[]};report.samples.push(sample)
  const context=await browser.newContext({viewport:{width,height},locale:'en-US'})
  await context.addInitScript(({hub,token,subnet})=>{
   if(!/^https?:$/.test(location.protocol))return
   for(const[key,value]of Object.entries({adaos_local_hub_url:hub,adaos_local_hub_token:token,
    adaos_local_subnet_id:subnet,adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'en'}))localStorage.setItem(key,value)
  },{hub:process.env.ADAOS_E2E_HUB_URL,token:process.env.ADAOS_E2E_HUB_TOKEN,subnet:process.env.ADAOS_E2E_SUBNET_ID})
  const page=await context.newPage();page.setDefaultTimeout(20000)
  await installSystemOverlayDismissal(page)
  page.on('pageerror',error=>sample.errors.push(String(error)))
  page.on('response',async response=>{
   if(new URL(response.url()).pathname!=='/api/resources/query')return
   const request=response.request().postDataJSON()
   if(!String(request?.resource_type||'').startsWith('prototype.project.'+project+'.'))return
   sample.queries.push({resource:request.resource_type,status:response.status(),result:await response.json().catch(()=>null)})
  })
  page.on('response',async response=>{
   if(new URL(response.url()).pathname!=='/api/resources/operate')return
   const request=response.request().postDataJSON()
   if(!String(request?.resource_type||'').startsWith('prototype.project.'+project+'.'))return
   report.operations.push({layout,request,status:response.status(),result:await response.json().catch(()=>null)})
  })
  const host=id=>page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).filter({visible:true}).last()
  const label=button=>typeof button.label==='string'?button.label:button.label?.en
  async function tab(name){
   const navigation=widgets.find(w=>w.inputs?.variant==='tabs'&&w.inputs.buttons?.some(b=>label(b)===name))
   assert(navigation,`No public ${name} tab`)
   const button=navigation.inputs.buttons.find(b=>label(b)===name)
   await host(navigation.id).locator(`[data-command-id=${JSON.stringify(button.id)}]`).click()
  }
  function teamProjection(){
   const work=locate('w1','title'),team=locate('T-A','name')
   const directory=semantic.views.find(v=>v.role==='collection'&&v.resource_ref===team.resource.id)
   const choices=semantic.views.filter(v=>v.role==='collection'&&v.resource_ref===work.resource.id
    &&v.selection_filter?.source_view_ref===directory.id)
   assert.equal(choices.length,1,'One source-owned secondary work-order projection is required')
   return {directory,view:choices[0],widget:widgets.find(w=>w.id===choices[0].id)}
  }
  async function check(name,action){try{await action();sample.checks.push({name,ok:true})}catch(error){sample.checks.push({name,ok:false,error:String(error)});throw error}}
  try{
   const url=new URL(process.env.ADAOS_E2E_CLIENT_URL)
   for(const[key,value]of Object.entries({intent:'webspace.open',zone:'lo',subnet_id:process.env.ADAOS_E2E_SUBNET_ID,
    webspace_id:input.preview_webspace_id,scenario_id:project,expected_scenario_id:project,expected_revision:revision,try_local_hub:'1'}))url.searchParams.set(key,value)
   await page.goto(url.href,{waitUntil:'domcontentloaded'})
   await expect(page.locator('.scenario-release-stage')).toHaveText('ALPHA',{timeout:60000})
   const materialization=await page.evaluate(()=>{const c=window.ng.getComponent(document.querySelector('app-root'));return c.ydoc.toJSON(c.ydoc.getPath('ui/application/desktop/pageSchema'))._adaos.materialization})
   assert.equal(materialization.stage,'prototype');assert.equal(materialization.revision,revision);sample.materialization=materialization
   if(checks.includes('team_work_orders'))await check('initial-empty-team-shows-all-orders',async()=>{
    const projected=teamProjection()
    assert.equal(projected.directory.initial_selection_id,null)
    assert.equal(projected.view.selection_filter.effect,'filter')
    assert.equal(projected.view.selection_filter.empty_selection,'show_all')
    await tab('Teams')
    await revealPrototypeWidget(page,widgets,projected.widget)
    const directory=widgets.find(w=>w.id===projected.directory.id)
    const selected=host(directory.id).locator('.selection-marker[aria-pressed="true"]')
    assert.equal(await selected.count(),0,'Revealing the secondary panel must not fabricate Team selection')
    await expect(host(projected.view.id).locator(prototypeRowSelector(projected.widget))).toHaveCount(3)
   })
   for(const record of records)for(const[sourceField,fixtureValue]of Object.entries(record.fields)){
    const source=locate(record.subject,sourceField);assert.deepEqual(source.fact.value,fixtureValue)
    const value=runtimeExpected(source,fixtureValue)
    const view=collectionFor(source)
    assert(view,`Source ${record.subject}.${sourceField} has no collection projection`)
    const widget=widgets.find(w=>w.id===view.id);assert(widget)
    await dismissSystemOverlays(page);await revealPrototypeWidget(page,widgets,widget)
    await check('retained-fact:'+record.subject+'.'+sourceField,async()=>{
     const row=await prototypeRowById(host(view.id),widget,String(source.record.id))
     const receipt=sample.queries.filter(q=>q.resource.endsWith('.'+source.resource.id)&&q.result?.items?.some(r=>String(r.id)===String(source.record.id))).at(-1)
     assert.equal(receipt?.status,200);assert.deepEqual(receipt.result.items.find(r=>String(r.id)===String(source.record.id))[source.fieldId],value)
     const{value:cell}=await prototypeFieldCell(row,widget,source.fieldId)
     const text=(await cell.innerText()).trim()
     if(typeof value==='boolean')assert.equal(await cell.locator('ion-checkbox').evaluate(e=>e.checked),value)
     else if(typeof value==='number')assert.equal(Number(text.replace(/[\s\u00a0]/g,'').replace(',','.')),value)
     else if(value===null)assert(['','-','—','null'].includes(text))
     else if(sourceField==='work_id'){
      const owner=records.find(record=>record.subject===value);assert(owner)
      assert.equal(text,runtimeExpected(locate(owner.subject,'title'),owner.fields.title))
     }
     else if(sourceField!=='team_id')assert(sourceScalarTextMatches(source.field,text,value),
       `Visible source value mismatch: ${record.subject}.${sourceField}: ${JSON.stringify(text)} != ${JSON.stringify(value)}`)
    })
   }
   const title=locate('w1','title'),view=collectionFor(title)
   const widget=widgets.find(w=>w.id===view.id)
   await revealPrototypeWidget(page,widgets,widget)
   if(checks.includes('labels'))await check('retained-labels',async()=>{
    await expect(host(view.id)).toContainText('Repair queue')
    const row=await prototypeRowById(host(view.id),widget,String(title.record.id))
    assert.equal(await prototypeFieldCaption(row,widget,title.fieldId),'Request')
   })
   if(checks.some(c=>['column_order','relative_column_order'].includes(c)))await check('retained-column-order',async()=>{
    const expected=['state','priority','title','hours'].map(field=>locate('w1',field).fieldId)
    assert.deepEqual(view.field_refs.filter(ref=>expected.includes(ref)),expected)
    const renderer=host(view.id).locator('ada-table-widget').first()
    const columns=await renderer.evaluate(el=>window.ng.getComponent(el).columns.map(c=>c.key))
    assert.deepEqual(columns.filter(key=>expected.includes(key)),expected)
   })
   if(checks.includes('search'))await check('retained-search-and-scope',async()=>{
    const toolbar=host('queries-'+view.id),search=toolbar.locator('input[type=search]')
    for(const[query,count]of[['pump',1],['accepted',0],['high',0]]){
     await search.fill(query)
     await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(count)
    }
    await toolbar.locator('.query-toolbar__reset').click()
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(3)
   })
   if(checks.includes('priority_filter'))await check('retained-filter-composition',async()=>{
    const toolbar=host('queries-'+view.id)
    await toolbar.locator('.query-toolbar__toggle').click()
    const select=toolbar.locator('select').first()
    const option=select.locator('option').filter({hasText:/^low$/i}).first()
    await select.selectOption(await option.getAttribute('value'))
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(1)
    await toolbar.locator('input[type=search]').fill('pump')
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(0)
    await toolbar.locator('.query-toolbar__reset').click()
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(3)
   })
   if(checks.includes('teams'))await check('teams-directory',async()=>{
    const team=locate('T-A','name')
    assert.notEqual(team.resource.id,title.resource.id,'Teams must be a separate related directory')
    const directory=semantic.views.find(v=>v.role==='collection'&&v.resource_ref===team.resource.id)
    assert(directory);const component=widgets.find(w=>w.id===directory.id)
    await revealPrototypeWidget(page,widgets,component)
    await expect(host(directory.id).locator(prototypeRowSelector(component))).toHaveCount(2)
    await revealPrototypeWidget(page,widgets,widget)
   })
   if(checks.includes('related_names'))await check('source-owned-related-names',async()=>{
    const source=locate('w1','team_id'),target=locate('T-A','name')
    const relationships=semantic.relationships.filter(r=>r.from_resource_ref===source.resource.id
     &&r.from_field_ref===source.fieldId&&r.to_resource_ref===target.resource.id)
    assert.equal(relationships.length,1,'One exact relationship must own the foreign-key projection')
    for(const[subject,expected]of[['w1','Mechanical'],['w2','Electrical'],['w3','Mechanical']]){
     const row=await prototypeRowById(host(view.id),widget,String(locate(subject,'title').record.id))
     const cell=(await prototypeFieldCell(row,widget,source.fieldId)).value
     await expect(cell).toHaveText(expected)
    }
   })
   if(checks.includes('details'))await check('selection-owned-related-details',async()=>{
    const field=locate('w1','team_id').fieldId
    const candidates=semantic.views.filter(v=>v.role==='details'&&v.resource_ref===title.resource.id&&v.field_refs.includes(field))
    assert.equal(candidates.length,1)
    const detail=candidates[0],component=widgets.find(w=>w.id===detail.id);assert(component)
    for(const[subject,expected]of[['w1','Mechanical'],['w2','Electrical']]){
     await revealPrototypeWidget(page,widgets,widget)
     await selectPrototypeRow(await prototypeRowById(host(view.id),widget,String(locate(subject,'title').record.id)),{activate:true})
     await revealPrototypeWidget(page,widgets,component)
     const rows=host(detail.id).locator('.details-row')
     const index=()=>rows.evaluateAll((elements,id)=>elements.findIndex(el=>{
      const context=window.ng.getContext(el)
      return (context?.$implicit||context?.row)?.key===id
     }),field)
     await expect.poll(index).toBeGreaterThanOrEqual(0)
     await expect(rows.nth(await index()).locator('dd')).toHaveText(expected)
    }
    await revealPrototypeWidget(page,widgets,widget)
   })
   if(checks.includes('tabs'))await check('independent-tab-selection',async()=>{
    const directory=semantic.views.find(v=>v.role==='collection'&&v.resource_ref===locate('T-A','name').resource.id)
    const component=widgets.find(w=>w.id===directory.id)
    async function selected(collection,record){
     const row=await prototypeRowById(host(collection.id),collection,String(record.id))
     assert(await row.evaluate(el=>el.getAttribute('aria-selected')==='true'||el.classList.contains('row-selected')
      ||Boolean(el.querySelector('.selection-marker[aria-pressed="true"]'))),'Selection lost or leaked between resources')
    }
    await tab('Work orders')
    await selectPrototypeRowForCommand(page,widgets,widget,await prototypeRowById(host(view.id),widget,String(locate('w2','title').record.id)))
    await tab('Teams')
    await selectPrototypeRowForCommand(page,widgets,component,await prototypeRowById(host(directory.id),component,String(locate('T-A','name').record.id)))
    await tab('Work orders');await selected(widget,locate('w2','title').record)
    await selectPrototypeRowForCommand(page,widgets,widget,await prototypeRowById(host(view.id),widget,String(locate('w1','title').record.id)))
    await tab('Teams');await selected(component,locate('T-A','name').record)
    await tab('Work orders');await selected(widget,locate('w1','title').record)
   })
   if(checks.includes('team_work_orders'))await check('team-filter-does-not-leak-to-global-orders',async()=>{
    const projected=teamProjection(),directory=widgets.find(w=>w.id===projected.directory.id)
    await tab('Teams')
    for(const[subject,identities]of[['T-A',['w1','w3']],['T-B',['w2']]]){
     await revealPrototypeWidget(page,widgets,directory)
     await selectPrototypeRowForCommand(page,widgets,directory,
      await prototypeRowById(host(directory.id),directory,String(locate(subject,'name').record.id)))
     await revealPrototypeWidget(page,widgets,projected.widget)
     const rows=host(projected.view.id).locator(prototypeRowSelector(projected.widget))
     await expect(rows).toHaveCount(identities.length)
     for(const id of identities)await prototypeRowById(host(projected.view.id),projected.widget,String(locate(id,'title').record.id))
    }
    await revealPrototypeWidget(page,widgets,directory)
    await tab('Work orders')
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(3)
    const toolbar=host('queries-'+view.id)
    await toolbar.locator('input[type=search]').fill('pump')
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(1)
    await tab('Teams')
    await revealPrototypeWidget(page,widgets,projected.widget)
    await expect(host(projected.view.id).locator(prototypeRowSelector(projected.widget))).toHaveCount(1)
    await prototypeRowById(host(projected.view.id),projected.widget,String(locate('w2','title').record.id))
    await revealPrototypeWidget(page,widgets,directory)
    await tab('Work orders');await toolbar.locator('.query-toolbar__reset').click()
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(3)
   })
   if(checks.includes('checklists'))await check('independent-checklist-directory-and-link',async()=>{
    const source=locate('c1','work_id')
    assert.notEqual(source.resource.id,title.resource.id)
    const links=semantic.relationships.filter(r=>r.from_resource_ref===source.resource.id
     &&r.from_field_ref===source.fieldId&&r.to_resource_ref===title.resource.id&&r.to_field_ref==='id')
    assert.equal(links.length,1)
    const child=collectionFor(source),component=widgets.find(w=>w.id===child.id)
    await revealPrototypeWidget(page,widgets,component)
    await expect(host(child.id).locator(prototypeRowSelector(component))).toHaveCount(3)
    await revealPrototypeWidget(page,widgets,widget)
   })
   if(checks.includes('emphasis'))await check('emphasize-related-checklists-without-filtering',async()=>{
    const source=locate('c1','work_id'),child=collectionFor(source),component=widgets.find(w=>w.id===child.id)
    assert.equal(child.selection_filter?.source_view_ref,view.id)
    assert.equal(child.selection_filter?.effect,'emphasize')
    assert.equal(child.selection_filter?.field_ref,source.fieldId)
    for(const[subject,related]of[['w1',['c1','c2']],['w2',[]],['w3',['c3']]]){
     await revealPrototypeWidget(page,widgets,widget)
     await selectPrototypeRowForCommand(page,widgets,widget,
      await prototypeRowById(host(view.id),widget,String(locate(subject,'title').record.id)))
     await revealPrototypeWidget(page,widgets,component)
     await expect(host(child.id).locator(prototypeRowSelector(component))).toHaveCount(3)
     await expect(host(child.id).locator('tbody > tr.row-related')).toHaveCount(related.length)
     for(const id of ['c1','c2','c3']){
      const row=await prototypeRowById(host(child.id),component,String(locate(id,'work_id').record.id))
      assert.equal(await row.evaluate(el=>el.classList.contains('row-related')),related.includes(id))
     }
    }
    await revealPrototypeWidget(page,widgets,widget)
   })
   if(checks.includes('accept'))await check('retained-Accept',async()=>{
    const commands=semantic.commands.filter(c=>Object.values(c.label||{}).includes('Accept'))
    assert.equal(commands.length,1);const command=commands[0]
    const editor=widgets.find(w=>w.type==='ui.form'&&w.actions?.some(a=>a.params?.command_id===command.id||a.params?.operation_id===command.id))
      ||widgets.find(w=>w.type==='ui.form'&&JSON.stringify(w).includes(command.id))
    assert(editor,'No reachable command editor')
    const modal=Object.entries(webui.modals||{}).find(([,m])=>m.schema?.widgets?.some(w=>w.id===editor.id))?.[0]
    const opener=recordEditorOpener(widgets,modal,editor.inputs?.selectedStateKey)
    async function closeEditor(){
     const dialog=page.locator('ion-modal').filter({visible:true}).last()
     if(await dialog.isVisible()){
      await dialog.getByRole('button',{name:'Close',exact:true}).click()
      await expect(dialog).toBeHidden()
     }
     await revealPrototypeWidget(page,widgets,widget)
    }
    async function openFor(subject){
     await closeEditor()
     const source=locate(subject,'state'),row=await prototypeRowById(host(view.id),widget,String(source.record.id))
     await selectPrototypeRowForCommand(page,widgets,widget,row)
     if(opener){if(opener.collection){
      const entry=prototypeRowCommand(row,opener.commandId)
      if(!await entry.isVisible()||await entry.isDisabled())return null
      await entry.click()
     }else await host(opener.owner.id).locator(`[data-command-id=${JSON.stringify(opener.commandId)}]`).click()}
     else await revealPrototypeWidget(page,widgets,editor)
     // Opening an Ionic modal is asynchronous. A not-yet-rendered button is
     // not evidence that the command is correctly hidden for this record.
     await expect(host(editor.id)).toBeVisible()
     return host(editor.id).locator(`button[data-command-id=${JSON.stringify(command.id)}],[data-command-id=${JSON.stringify(command.id)}] button`).first()
    }
    const eligible=records.filter(r=>r.subject.startsWith('w')&&runtimeExpected(locate(r.subject,'state'),r.fields.state)==='open')
    for(const subject of records.filter(r=>r.subject.startsWith('w')&&!eligible.includes(r)).map(r=>r.subject)){
     const blocked=await openFor(subject)
     assert(blocked===null||!await blocked.isVisible()||await blocked.isDisabled(),`Accept must not be available for ${subject}`)
    }
    if(!eligible.length){sample.checks.push({name:'retained-Accept-no-open-live-records',ok:true,positive_mutation_coverage:'See retained level-3 wide eligibility/compact transition; this revision proves all stored non-open states are blocked.'});await closeEditor();return}
    assert.equal(eligible[0].subject,'w1','Preregister a new positive mutation probe before changing eligible identities')
    const button=await openFor('w1')
    assert(button!==null,'Accept opener must be available for an open record')
    await expect(button).toBeEnabled()
    // Both viewports first observe the same unmodified source fixture. Only
    // the last viewport mutates; otherwise the compact probe sees our own
    // earlier Accept and falsely reports source-data loss.
    if(layout==='wide'){await closeEditor();return}
    await button.click()
    const alert=page.locator('ion-alert').filter({visible:true}).last()
    if(await alert.isVisible())await alert.locator('button').last().click()
    await closeEditor()
    const row=await prototypeRowById(host(view.id),widget,String(title.record.id))
    const state=locate('w1','state')
    const cell=(await prototypeFieldCell(row,widget,state.fieldId)).value
    await expect.poll(async()=>sourceScalarTextMatches(state.field,(await cell.innerText()).trim(),'accepted')).toBe(true)
    await expect(row).toContainText('Replace pump')
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(3)
    const after=sample.queries.filter(q=>q.resource.endsWith('.'+title.resource.id)&&q.result?.items?.length===3).at(-1)?.result.items
    assert(after,'No read-after-write evidence for all retained work orders')
    for(const fixture of records.filter(r=>r.subject.startsWith('w')))for(const[field,value]of Object.entries(fixture.fields)){
     const source=locate(fixture.subject,field)
     assert.deepEqual(after.find(r=>r.id===source.record.id)?.[source.fieldId],fixture.subject==='w1'&&field==='state'?'accepted':runtimeExpected(source,value))
    }
    const repeated=await openFor('w1')
    assert(repeated===null||!await repeated.isVisible()||await repeated.isDisabled(),'A stored accepted record must no longer expose an available Accept')
    await closeEditor()
   })
   if(checks.includes('hours_chart'))await check('independent-source-hours-chart',async()=>{
    const hours=locate('w1','hours')
    const charts=semantic.views.filter(v=>v.role==='collection'&&v.resource_ref===title.resource.id&&v.presentation==='chart')
    assert.equal(charts.length,1,'One separate chart of the existing resource is required')
    const chart=charts[0],component=widgets.find(w=>w.id===chart.id)
    assert.equal(chart.section?.id,view.section?.id,'Chart belongs to the existing Work orders tab')
    assert.equal(chart.presentation_options.group_field_ref,title.fieldId)
    assert.equal(chart.presentation_options.value_field_ref,hours.fieldId)
    assert.equal(component.type,'visual.metricChart')
    assert(!chart.selection_filter&&!(chart.scope_filters||[]).length,'Chart is not a filtered projection')
    const expected=records.filter(row=>['w1','w2','w3'].includes(row.subject)).map(record=>{
     const x=locate(record.subject,'title'),y=locate(record.subject,'hours')
     return {x:runtimeExpected(x,record.fields.title),y:runtimeExpected(y,record.fields.hours)}
    }).sort((a,b)=>a.x.localeCompare(b.x))
    async function chartPoints(){
     await revealPrototypeWidget(page,widgets,component)
     const renderer=host(chart.id).locator('ada-metric-chart-widget')
     await expect.poll(()=>renderer.evaluate(el=>window.ng.getComponent(el).series.points.map(p=>({x:p.x,y:p.y}))
      .sort((a,b)=>a.x.localeCompare(b.x)))).toEqual(expected)
     await expect(renderer.locator('svg circle.dot')).toHaveCount(3)
    }
    await chartPoints()
    await revealPrototypeWidget(page,widgets,widget)
    const toolbar=host('queries-'+view.id)
    await toolbar.locator('input[type=search]').fill('pump')
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(1)
    await chartPoints()
    await revealPrototypeWidget(page,widgets,widget)
    const select=toolbar.locator('select').first()
    if(!await select.isVisible())await toolbar.locator('.query-toolbar__toggle').click()
    const option=select.locator('option').filter({hasText:/^low$/i}).first()
    await select.selectOption(await option.getAttribute('value'))
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(0)
    await chartPoints()
    await revealPrototypeWidget(page,widgets,widget)
    await toolbar.locator('.query-toolbar__reset').click()
    const projected=teamProjection(),directory=widgets.find(w=>w.id===projected.directory.id)
    await tab('Teams');await revealPrototypeWidget(page,widgets,directory)
    await selectPrototypeRowForCommand(page,widgets,directory,
     await prototypeRowById(host(directory.id),directory,String(locate('T-B','name').record.id)))
    await revealPrototypeWidget(page,widgets,projected.widget)
    await expect(host(projected.view.id).locator(prototypeRowSelector(projected.widget))).toHaveCount(1)
    await tab('Work orders');await chartPoints()
    await revealPrototypeWidget(page,widgets,widget)
   })
   if(checks.includes('state_board'))await check('read-only-board-groups-and-shared-global-selection',async()=>{
    const state=locate('w1','state'),priority=locate('w1','priority'),hours=locate('w1','hours')
    const boards=semantic.views.filter(v=>v.role==='collection'&&v.resource_ref===title.resource.id&&v.presentation==='board')
    assert.equal(boards.length,1)
    const board=boards[0],component=widgets.find(w=>w.id===board.id)
    assert.equal(board.section?.id,view.section?.id)
    assert.equal(board.presentation_options.group_field_ref,state.fieldId)
    assert.equal(board.presentation_options.draggable,false)
    assert.equal(component.type,'collection.board');assert.equal(component.inputs.dragDrop,false)
    assert(!component.actions?.some(a=>a.on==='move'||a.type==='resourceOperation'),'Board cannot expose writes')
    assert(component.actions?.some(action=>action.on==='select'&&action.type==='updateState'
     &&action.params?.[widget.inputs.selectedStateKey]==='$event.id'),
     'Board select event writes the global cursor; Board has no selectedStateKey input')
    const expected=records.filter(r=>['w1','w2','w3'].includes(r.subject)).map(r=>({
     id:String(locate(r.subject,'title').record.id),state:String(runtimeExpected(locate(r.subject,'state'),r.fields.state))
    })).sort((a,b)=>a.id.localeCompare(b.id))
    async function boardRows(){
     await revealPrototypeWidget(page,widgets,component)
     const renderer=host(board.id).locator('ada-board-widget')
     await expect.poll(()=>renderer.evaluate(el=>window.ng.getComponent(el).laneProjections
      .flatMap(lane=>lane.items.map(item=>({id:String(item.id),state:lane.id})))
      .sort((a,b)=>a.id.localeCompare(b.id)))).toEqual(expected)
     await expect(renderer.locator('[data-webui-board-item-id]')).toHaveCount(3)
     for(const source of records.filter(r=>['w1','w2','w3'].includes(r.subject))){
      const card=renderer.locator(`[data-webui-board-item-id=${JSON.stringify(String(locate(source.subject,'title').record.id))}]`)
      await expect(card.locator('.board-card__title')).toHaveText(runtimeExpected(locate(source.subject,'title'),source.fields.title))
      const values=await card.locator('.board-card__field-value').allTextContents()
      assert(values.some(value=>Number(value.trim().replace(',','.'))===runtimeExpected(locate(source.subject,'hours'),source.fields.hours)))
      assert(values.some(value=>sourceScalarTextMatches(priority.field,value.trim(),runtimeExpected(locate(source.subject,'priority'),source.fields.priority))))
     }
     assert.equal(await renderer.evaluate(el=>window.ng.getComponent(el).dragDrop),false)
     assert.equal(await renderer.locator('.board-card__drag-handle,.board-card__move').count(),0)
    }
    await boardRows()
    await revealPrototypeWidget(page,widgets,widget)
    const toolbar=host('queries-'+view.id)
    await toolbar.locator('input[type=search]').fill('pump')
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(1)
    await boardRows();await revealPrototypeWidget(page,widgets,widget)
    await toolbar.locator('.query-toolbar__reset').click()
    const checklistSource=locate('c1','work_id'),checklist=collectionFor(checklistSource)
    const checklistWidget=widgets.find(w=>w.id===checklist.id)
    const team=semantic.views.find(v=>v.role==='collection'&&v.resource_ref===locate('T-A','name').resource.id)
    const teamWidget=widgets.find(w=>w.id===team.id)
    await tab('Teams');await revealPrototypeWidget(page,widgets,teamWidget)
    await selectPrototypeRowForCommand(page,widgets,teamWidget,
     await prototypeRowById(host(team.id),teamWidget,String(locate('T-A','name').record.id)))
    await tab('Work orders');await boardRows()
    const target=host(board.id).locator(`[data-webui-board-item-id=${JSON.stringify(String(locate('w3','title').record.id))}] .board-card__main`)
    await target.click()
    await expect(target).toHaveAttribute('aria-pressed','true')
    const selectedCard=target.locator('xpath=ancestor::*[@data-webui-board-item-id][1]')
    await expect(selectedCard).toHaveClass(/board-card--selected/)
    await expect.poll(()=>selectedCard.evaluate(element=>{
     const style=getComputedStyle(element)
     return parseFloat(style.outlineWidth)>0&&style.outlineStyle!=='none'
    })).toBe(true)
    await revealPrototypeWidget(page,widgets,widget)
    const selected=await prototypeRowById(host(view.id),widget,String(locate('w3','title').record.id))
    await expect.poll(()=>selected.evaluate(el=>el.getAttribute('aria-selected')==='true'||el.classList.contains('row-selected')
      ||Boolean(el.querySelector('.selection-marker[aria-pressed="true"]')))).toBe(true)
    await revealPrototypeWidget(page,widgets,checklistWidget)
    await expect(host(checklist.id).locator(prototypeRowSelector(checklistWidget))).toHaveCount(3)
    await expect(host(checklist.id).locator('tbody > tr.row-related')).toHaveCount(1)
    const related=await prototypeRowById(host(checklist.id),checklistWidget,String(locate('c3','work_id').record.id))
    assert(await related.evaluate(el=>el.classList.contains('row-related')))
    await tab('Teams');await revealPrototypeWidget(page,widgets,teamWidget)
    const retained=await prototypeRowById(host(team.id),teamWidget,String(locate('T-A','name').record.id))
    assert(await retained.evaluate(el=>el.getAttribute('aria-selected')==='true'||el.classList.contains('row-selected')
      ||Boolean(el.querySelector('.selection-marker[aria-pressed="true"]'))))
    await tab('Work orders');await revealPrototypeWidget(page,widgets,widget)
    // Selection must flow back from another renderer into the Board's visible
    // consumer, not merely dispatch a Board event into the global collection.
    await selectPrototypeRow(await prototypeRowById(host(view.id),widget,String(locate('w2','title').record.id)))
    await boardRows()
    const fromTable=host(board.id).locator(`[data-webui-board-item-id=${JSON.stringify(String(locate('w2','title').record.id))}] .board-card__main`)
    await expect(fromTable).toHaveAttribute('aria-pressed','true')
    await expect(fromTable.locator('xpath=ancestor::*[@data-webui-board-item-id][1]')).toHaveClass(/board-card--selected/)
    await expect(target).toHaveAttribute('aria-pressed','false')
   })
   // Further level-specific checks must be implemented before paying for that level.
   const covered=new Set(['labels','column_order','relative_column_order','search','priority_filter','accept',
    'teams','related_names','details','tabs','selection_isolation','team_work_orders','checklists','emphasis','hours_chart','state_board'])
   for(const check of checks)assert(covered.has(check),'Missing independent browser oracle: '+check)
   await check('no-page-overflow',async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2)))
   await dismissSystemOverlays(page)
   await page.screenshot({path:path.join(output,layout+'.png'),fullPage:true})
  }catch(error){sample.errors.push(String(error));await page.screenshot({path:path.join(output,layout+'-failure.png'),fullPage:true}).catch(()=>{})}
  await context.close()
 }
 report.ok=report.samples.length===2&&report.samples.every(s=>!s.errors.length&&s.checks.every(c=>c.ok))
}catch(error){report.errors.push(String(error))}
finally{await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2),{flag:'wx'});await browser.close()}
console.log(JSON.stringify({ok:report.ok,project,revision,samples:report.samples.map(s=>({layout:s.layout,checks:s.checks.length,errors:s.errors})),errors:report.errors}))
if(!report.ok)process.exitCode=1
