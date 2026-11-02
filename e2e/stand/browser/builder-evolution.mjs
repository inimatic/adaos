import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { sourceScalarTextMatches } from './prototype-source-scalar.mjs'
import { prototypeRowById, prototypeFieldCell, prototypeFieldCaption, prototypeRowSelector,
  revealPrototypeWidget, installSystemOverlayDismissal, dismissSystemOverlays,
  selectPrototypeRowForCommand, recordEditorOpener, prototypeRowCommand } from './prototype-navigation.mjs'

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
  page.on('request',request=>{if(new URL(request.url()).pathname==='/api/resources/operate')report.operations.push({layout,mutation:true})})
  const host=id=>page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).filter({visible:true}).last()
  async function check(name,action){try{await action();sample.checks.push({name,ok:true})}catch(error){sample.checks.push({name,ok:false,error:String(error)});throw error}}
  try{
   const url=new URL(process.env.ADAOS_E2E_CLIENT_URL)
   for(const[key,value]of Object.entries({intent:'webspace.open',zone:'lo',subnet_id:process.env.ADAOS_E2E_SUBNET_ID,
    webspace_id:input.preview_webspace_id,scenario_id:project,expected_scenario_id:project,expected_revision:revision,try_local_hub:'1'}))url.searchParams.set(key,value)
   await page.goto(url.href,{waitUntil:'domcontentloaded'})
   await expect(page.locator('.scenario-release-stage')).toHaveText('ALPHA',{timeout:60000})
   const materialization=await page.evaluate(()=>{const c=window.ng.getComponent(document.querySelector('app-root'));return c.ydoc.toJSON(c.ydoc.getPath('ui/application/desktop/pageSchema'))._adaos.materialization})
   assert.equal(materialization.stage,'prototype');assert.equal(materialization.revision,revision);sample.materialization=materialization
   for(const record of records)for(const[sourceField,value]of Object.entries(record.fields)){
    const source=locate(record.subject,sourceField);assert.deepEqual(source.fact.value,value)
    const view=semantic.views.find(v=>v.role==='collection'&&v.resource_ref===source.resource.id&&v.field_refs.includes(source.fieldId))
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
     else if(sourceField!=='team_id')assert(sourceScalarTextMatches(source.field,text,value),
       `Visible source value mismatch: ${record.subject}.${sourceField}: ${JSON.stringify(text)} != ${JSON.stringify(value)}`)
    })
   }
   const title=locate('w1','title'),view=semantic.views.find(v=>v.role==='collection'&&v.resource_ref===title.resource.id&&v.field_refs.includes(title.fieldId))
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
   if(checks.includes('accept'))await check('retained-Accept',async()=>{
    const commands=semantic.commands.filter(c=>Object.values(c.label||{}).includes('Accept'))
    assert.equal(commands.length,1);const command=commands[0]
    const row=await prototypeRowById(host(view.id),widget,String(title.record.id))
    await selectPrototypeRowForCommand(page,widgets,widget,row)
    const editor=widgets.find(w=>w.type==='ui.form'&&w.actions?.some(a=>a.params?.command_id===command.id||a.params?.operation_id===command.id))
      ||widgets.find(w=>w.type==='ui.form'&&JSON.stringify(w).includes(command.id))
    assert(editor,'No reachable command editor')
    const modal=Object.entries(webui.modals||{}).find(([,m])=>m.schema?.widgets?.some(w=>w.id===editor.id))?.[0]
    const opener=recordEditorOpener(widgets,modal,editor.inputs?.selectedStateKey)
    if(opener){if(opener.collection)await prototypeRowCommand(row,opener.commandId).click();else await host(opener.owner.id).locator(`[data-command-id=${JSON.stringify(opener.commandId)}]`).click()}
    else await revealPrototypeWidget(page,widgets,editor)
    const button=host(editor.id).locator(`[data-command-id=${JSON.stringify(command.id)}]`).first()
    if(await button.isDisabled()){sample.checks.push({name:'repeat-Accept-disabled',ok:true});return}
    await button.click()
    const alert=page.locator('ion-alert').filter({visible:true}).last()
    if(await alert.isVisible())await alert.locator('button').last().click()
    await expect(row).toContainText('accepted')
    await expect(row).toContainText('Replace pump')
    await expect(host(view.id).locator(prototypeRowSelector(widget))).toHaveCount(3)
   })
   // Further level-specific checks must be implemented before paying for that level.
   const covered=new Set(['labels','column_order','relative_column_order','search','priority_filter','accept'])
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
