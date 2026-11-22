// Native catalog/preview reads; settings writes and paid inference are fixtures.
import {createRequire} from 'node:module'
import fs from 'node:fs/promises'
const require=createRequire(process.cwd()+'/e2e/stand/browser/entry.mjs'),{chromium,expect}=require('@playwright/test')
const app='site_studio_test_20261003_e2eadc45d49732_26215830'
const token=process.env.ADAOS_E2E_HUB_TOKEN,release=process.env.ADAOS_E2E_EXPECT_RELEASE,output=process.env.ADAOS_E2E_OUTPUT
if(!token||!release||!output)throw Error('Candidate and local control authority required')
await fs.mkdir(output,{recursive:true})
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1700,height:1100},locale:'ru-RU'})
await context.addInitScript(({token})=>{
 if(location.origin!=='http://localhost:8100')return
 window.__ADAOS_DEBUG__=true;window.__ADAOS_BASE__='http://127.0.0.1:8777';window.__ADAOS_TOKEN__=token
 for(const[k,v]of Object.entries({adaos_device_id:'site-execution-qualification',adaos_webspace_id:'dev2',adaos_hub_base:'http://127.0.0.1:8777',adaos_local_hub_base:'http://127.0.0.1:8777',adaos_try_local_hub:'1',adaos_hub_token:token,adaos_local_subnet_id:'sn_6acf0c01',adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'ru'}))localStorage.setItem(k,v)
},{token})
const page=await context.newPage(),report={passed:false,checks:[],errors:[],catalog:[],writes:[]}
let fixtureSaved=null
const widget=id=>page.locator(`[data-webui-widget-id="${id}"]`).last()
const fulfill=(route,result)=>route.fulfill({status:200,contentType:'application/json',headers:{'x-adaos-release-digest':release},body:JSON.stringify({ok:true,result})})
page.on('pageerror',e=>report.errors.push(e.message))
await page.addLocatorHandler(page.getByRole('button',{name:'Продолжить проверку',exact:true}),async l=>l.click())
await page.route('**/api/tools/call',async route=>{
 const body=route.request().postDataJSON(),tool=String(body?.tool||'')
 if(tool.endsWith(':save_ai_settings')){
  report.writes.push({tool,arguments:body.arguments})
  fixtureSaved={...body.arguments,id:body.arguments.project_id,revision:body.arguments.expected_revision+1}
  return fulfill(route,{ok:true,item:fixtureSaved})
 }
 if(/:(?:set_internet_access|set_node_image_policy|add_image_from_url|discuss|continue_authoring|mutate_|publish|prepare_|commit_|request_domain|activate_domain|select_project|create_project)/.test(tool)){
  report.writes.push({tool,blocked:true});return route.fulfill({status:409,body:'qualification_read_only'})
 }
 const response=await route.fetch()
 if(tool.endsWith(':query_ai_settings')){
  const json=await response.json(),value=json.result||json
  report.catalog=(value.items||[]).map(x=>({id:x.id,label:x.label}))
  if(fixtureSaved){value.item={...value.item,...fixtureSaved};value.settings_records=[value.item];return route.fulfill({response,json})}
 }
 return route.fulfill({response})
})
try{
 await page.goto('http://localhost:8100/?scenario_id='+app+'&webspace_id=dev2&webspace=dev2&try_local_hub=1',{waitUntil:'domcontentloaded',timeout:90000})
 const tabs=widget('authoring_tools')
 await expect(tabs).toBeVisible({timeout:140000})
 let settings=tabs.getByRole('button',{name:'Настройки',exact:true})
 if(!await settings.isVisible()){
  await tabs.getByRole('button',{name:/Ещё|Еще|More/}).click()
  settings=page.getByText('Настройки',{exact:true}).last()
 }
 await settings.click()
 const form=widget('site_ai_settings')
 await expect(form).toBeVisible({timeout:60000})
 await expect(form).toContainText('Модель и источник оплаты')
 await expect.poll(()=>report.catalog.length,{timeout:60000}).toBeGreaterThan(0)
 report.checks.push('Settings tab renders the actual Core API/connected ChatGPT catalog')
 const api=report.catalog.find(x=>x.id.startsWith('root::'))
 expect(api).toBeTruthy()
 const select=form.locator('select').first()
 await select.selectOption({label:api.label})
 const numbers=form.locator('input[type="number"]')
 await expect(numbers.nth(0)).toHaveValue('6000',{timeout:30000})
 await expect(numbers.nth(1)).toHaveValue('16')
 await numbers.nth(0).fill('7000');await numbers.nth(1).fill('8')
 await form.getByRole('button',{name:'Сохранить настройки',exact:true}).click()
 await expect.poll(()=>report.writes.filter(x=>x.tool.endsWith(':save_ai_settings')).length,{timeout:30000}).toBe(1)
 const saved=report.writes.find(x=>x.tool.endsWith(':save_ai_settings')).arguments
 expect(saved.choice_id).toBe(api.id);expect(saved.max_output_tokens).toBe(7000);expect(saved.max_steps).toBe(8)
 expect(Number.isInteger(saved.expected_revision)).toBe(true)
 expect(saved.reasoning_effort).toBe('default')
 expect(saved.id).toBe(saved.project_id);expect(saved.resource).toBe('ai_settings')
 expect(saved.payload.max_steps).toBe(8)
 await numbers.nth(1).fill('9')
 await form.getByRole('button',{name:'Сохранить настройки',exact:true}).click()
 await expect.poll(()=>report.writes.filter(x=>x.tool.endsWith(':save_ai_settings')).length,{timeout:30000}).toBe(2)
 const again=report.writes.filter(x=>x.tool.endsWith(':save_ai_settings'))[1].arguments
 expect(again.expected_revision).toBe(saved.expected_revision+1)
 expect(again.max_steps).toBe(9)
 report.checks.push('Model selection and numeric execution limits produce a revision-checked save; write intercepted')
 report.checks.push('The returned record receipt advances the form revision for a second save without reload')
 await page.screenshot({path:output+'/settings.png'})
 let materials=tabs.getByRole('button',{name:'Материалы',exact:true})
 if(!await materials.isVisible()){
  await tabs.getByRole('button',{name:/Ещё|Еще|More/}).click()
  materials=page.getByText('Материалы',{exact:true}).last()
 }
 await materials.click()
 await widget('materials_tools').getByRole('button',{name:'Медиа сайта',exact:true}).click()
 await expect(widget('internet_image_intake')).toBeVisible({timeout:30000})
 await expect(widget('internet_image_intake')).toContainText('JPEG, PNG или WebP')
 await expect(widget('internet_image_intake')).toContainText('приватные материалы проекта')
 report.checks.push('Image URL intake exposes explicit consent and supported formats')
 await page.screenshot({path:output+'/materials.png'})
 await expect(widget('site_preview')).toContainText('Applied',{timeout:100000})
 report.checks.push('Actual isolated site preview still renders')
 expect(report.errors).toEqual([])
 expect(report.writes.filter(x=>x.blocked)).toEqual([])
 report.passed=true
}catch(e){report.error=e.message;await page.screenshot({path:output+'/failure.png'}).catch(()=>{})}
finally{
 report.disclosure='Catalog, project and preview reads use the selected native Candidate. Settings save is intercepted to preserve user preferences. No provider inference or durable site changes are performed by this browser check.'
 await page.unrouteAll({behavior:'ignoreErrors'});await browser.close()
 await fs.writeFile(output+'/report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report))
}
if(!report.passed)process.exitCode=1
