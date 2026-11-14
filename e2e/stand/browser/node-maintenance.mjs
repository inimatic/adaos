// Requirement-delta acceptance for the Management maintenance surface.
// All mutation is placement intent; this runner never migrates data or accepts Stable.
import {chromium,expect as baseExpect} from '@playwright/test'
import fs from 'node:fs/promises'
import {dismissApplicationSystemOverlay,revealApplicationTarget} from './application-navigation.mjs'
const env=process.env
const expect=baseExpect.configure({timeout:45000})
if(env.ENV_TYPE!=='dev'||!env.ADAOS_E2E_HUB_TOKEN||!env.ADAOS_E2E_RELEASE_DIGEST||!env.ADAOS_E2E_DESIRED_PATH)throw new Error('Explicit owned DEV Beta, release identity and placement intent are required')
const output=env.ADAOS_E2E_OUTPUT
await fs.mkdir(output,{recursive:true})
const report={schema:'adaos.node.maintenance_browser.v1',release_digest:env.ADAOS_E2E_RELEASE_DIGEST,samples:[],moves_data:false}
const browser=await chromium.launch({headless:true})
try{
 for(const[layout,viewport]of Object.entries({wide:{width:1440,height:1000},compact:{width:390,height:844}})){
  const sample={layout,checks:[],errors:[],tools:[]};report.samples.push(sample)
  const context=await browser.newContext({viewport})
  const hub=env.ADAOS_E2E_HUB_URL,token=env.ADAOS_E2E_HUB_TOKEN,subnet=env.ADAOS_E2E_SUBNET_ID
  await context.addInitScript(({hub,token,subnet})=>{
   window.__ADAOS_DEBUG__=true;window.__ADAOS_BASE__=hub;window.__ADAOS_FIXED_RUNTIME_BASE__=hub;window.__ADAOS_TOKEN__=token
   for(const[k,v]of Object.entries({adaos_device_id:'node-maintenance-qualification',adaos_webspace_id:'desktop',adaos_hub_base:hub,adaos_local_hub_base:hub,adaos_try_local_hub:'1',adaos_hub_token:token,adaos_local_subnet_id:subnet,adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_env_type:'dev',adaos_lang:'en'}))localStorage.setItem(k,v)
  },{hub,token,subnet})
  const page=await context.newPage();page.setDefaultTimeout(45000)
  const pending=[]
  page.on('pageerror',error=>sample.errors.push(error.message))
  page.on('response',response=>{
   if(new URL(response.url()).pathname!=='/api/tools/call')return
   pending.push(response.json().then(body=>sample.tools.push({status:response.status(),
    request:response.request().postDataJSON(),runtime_source:response.headers()['x-adaos-runtime-source'],
    release_digest:response.headers()['x-adaos-release-digest'],result:body})).catch(()=>{}))
  })
  const host=id=>page.locator(`[data-webui-widget-id="${id}"]`).last()
  const reveal=async id=>{
   await dismissApplicationSystemOverlay(page,sample)
   const target=host(id);await revealApplicationTarget(page,target,sample,{layout})
   await expect(target).toBeVisible();await target.scrollIntoViewIfNeeded();return target
  }
  const command=async(id,button)=>{
   await reveal(id)
   const target=host(id).locator(`[data-command-id="${button}"]`).first()
   if(!await target.isVisible()){
    await host(id).locator('button:not([data-command-id]):visible').last().click()
    await page.locator(`[data-command-id="${button}"]:visible`).last().click()
   }else await target.click()
  }
  const url=new URL(env.ADAOS_E2E_CLIENT_URL||'http://127.0.0.1:8100/')
  for(const[k,v]of Object.entries({intent:'webspace.open',zone:'lo',subnet_id:subnet,webspace_id:'desktop',space_kind:'workspace',expected_scenario_id:'web_desktop',adaos_hub_base:hub,try_local_hub:'1',runtime_debug:'1'}))url.searchParams.set(k,v)
  try{
   sample.active='exact-beta'
   await page.goto(url.href,{waitUntil:'domcontentloaded'})
   await page.waitForFunction(digest=>window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.applicationReleaseDigest===digest,env.ADAOS_E2E_RELEASE_DIGEST,{timeout:90000})
   await dismissApplicationSystemOverlay(page,sample,{waitMs:5000})
   sample.materialization=await page.evaluate(()=>window.__ADAOS_DEBUG_STATE__().sync.materialization)
   sample.checks.push('exact-beta')
   sample.active='current-storage'
   await command('primary-nav','system')
   await reveal('maintenance-navigation')
   await expect(host('maintenance-locations')).toContainText('E2E evidence',{timeout:45000})
   await expect(host('maintenance-locations')).toContainText(/Not measured|GiB/)
   sample.checks.push('current-storage-visible')
   await page.screenshot({path:`${output}/${layout}-storage.png`,fullPage:true})
   sample.active='review-placement'
   await host('maintenance-locations').getByText('E2E evidence',{exact:true}).click()
   const form=await reveal('maintenance-location-form')
   const input=form.locator('[data-webui-field-id="desired_path"]').locator('input')
   await input.fill(env.ADAOS_E2E_DESIRED_PATH)
   await form.getByRole('button',{name:'Review location',exact:true}).click()
   await expect(host('maintenance-location-review')).toContainText(/True|true|Yes|Да/)
   const save=form.getByRole('button',{name:'Save desired location',exact:true})
   await expect(save).toBeEnabled()
   sample.checks.push('review-before-write')
   if(env.ADAOS_E2E_SAVE_PLACEMENT_INTENT==='1'){
    sample.active='sequential-acknowledged-saves'
    for(let iteration=0;iteration<2;iteration++){
     if(iteration)await form.getByRole('button',{name:'Review location',exact:true}).click()
     await expect(save).toBeEnabled()
     const acknowledged=page.waitForResponse(response=>{
      if(new URL(response.url()).pathname!=='/api/tools/call')return false
      return String(response.request().postDataJSON()?.tool||'').endsWith(':save_storage_location')
     })
     await save.click()
     const response=await acknowledged
     expect(response.status()).toBe(200)
     const body=await response.json();expect(body.ok).toBe(true)
     const receipt=body.result
     expect(receipt.status).toBe('desired_saved');expect(receipt.moves_data).toBe(false)
     expect(receipt.switches_writers).toBe(false)
     sample.receipts??=[]
     if(sample.receipts.length)expect(receipt.revision).toBe(sample.receipts.at(-1).revision+1)
     sample.receipts.push(receipt)
     await expect(host('maintenance-location-receipt')).toContainText('desired_saved')
     await expect(host('maintenance-location-receipt')).toContainText(String(receipt.revision))
     await expect(save).toBeDisabled()
    }
    sample.checks.push('two-acknowledged-intent-saves')
   }
   sample.active='selection-isolation'
   const tabs=host('system-member-tabs')
   const offline=tabs.locator('[data-command-id^="member:"]').first()
   if(await offline.count()){
    const offlineId=await offline.getAttribute('data-command-id')
    const hubId=await tabs.locator('[data-command-id^="hub:"]').first().getAttribute('data-command-id')
    await command('system-member-tabs',offlineId);await expect(host('maintenance-navigation')).not.toBeVisible()
    await command('system-member-tabs',hubId)
    await expect(host('maintenance-navigation')).toBeVisible()
    sample.checks.push('offline-node-hides-operations')
   }else sample.checks.push('selection-isolation-not-applicable-single-node')
   sample.active='recovery-and-tests'
   await command('maintenance-navigation','recovery')
   await reveal('maintenance-recovery-tools')
   await expect(host('maintenance-recovery-tools')).toContainText(/applications.lifecycle|Application/)
   await expect(host('maintenance-recovery-points')).toContainText(/not_verified|owner_receipts_required|No records/)
   await command('maintenance-navigation','testing')
   await reveal('maintenance-test-suites')
   await expect(host('maintenance-test-suites')).toContainText(/observe|Smoke|smoke/)
   await expect(host('maintenance-test-campaigns')).toContainText(/passed|failed|pending|No records/)
   sample.checks.push('recovery-and-testing-visible')
   sample.active='reopen'
   await page.reload({waitUntil:'domcontentloaded'})
   await page.waitForFunction(digest=>window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.applicationReleaseDigest===digest,env.ADAOS_E2E_RELEASE_DIGEST,{timeout:90000})
   await dismissApplicationSystemOverlay(page,sample,{waitMs:3000})
   await command('primary-nav','system');await command('maintenance-navigation','storage')
   await reveal('maintenance-locations')
   if(env.ADAOS_E2E_SAVE_PLACEMENT_INTENT==='1')await expect(host('maintenance-locations')).toContainText(/W:/i)
   sample.checks.push('reopen-authoritative-settings')
   await Promise.all(pending)
   const maintenanceCalls=sample.tools.filter(t=>/:(get_node_maintenance|plan_storage_location|save_storage_location|plan_validation_campaign|start_validation_campaign)$/.test(t.request?.tool||''))
   expect(maintenanceCalls.length).toBeGreaterThan(0)
   expect(maintenanceCalls.every(t=>t.status===200&&t.runtime_source==='trial'&&t.release_digest===env.ADAOS_E2E_RELEASE_DIGEST)).toBe(true)
   expect(sample.errors).toEqual([])
   sample.checks.push('tools-execute-from-exact-trial');sample.ok=true
  }catch(error){sample.failure=String(error);sample.text=(await page.locator('body').innerText()).slice(-24000);sample.debug=await page.evaluate(()=>window.__ADAOS_DEBUG_STATE__?.());await page.screenshot({path:`output/${layout}-failure.png`.replace('output/',output+'/'),fullPage:true}).catch(()=>{})}
  finally{await Promise.all(pending);await context.close();await fs.writeFile(output+'/report.json',JSON.stringify(report,null,2))}
 }
}finally{await browser.close();report.ok=report.samples.length===2&&report.samples.every(s=>s.ok);await fs.writeFile(output+'/report.json',JSON.stringify(report,null,2))}
console.log(JSON.stringify({ok:report.ok,samples:report.samples.map(s=>({layout:s.layout,checks:s.checks,failure:s.failure,active:s.active}))}))
if(!report.ok)process.exitCode=1
