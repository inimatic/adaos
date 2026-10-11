// Read-only diagnosis of the current warm Client (no explicit scenario target).
import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'
const env=process.env
if(env.ENV_TYPE!=='dev')throw new Error('DEV only')
const output=path.resolve(env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output,{recursive:true})
const browser=await chromium.launch({headless:true})
try {
 const context=await browser.newContext({viewport:{width:1440,height:1000}})
 await context.addInitScript(({hub,token,subnet,webspace})=>{
  window.__ADAOS_DEBUG__=true;window.__ADAOS_BASE__=hub;window.__ADAOS_TOKEN__=token
  for(const[key,value]of Object.entries({adaos_webspace_id:webspace,adaos_hub_base:hub,
   adaos_local_hub_base:hub,adaos_try_local_hub:'1',adaos_hub_token:token,adaos_local_subnet_id:subnet,
   adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'ru'}))localStorage.setItem(key,value)
 },{hub:env.ADAOS_E2E_HUB_URL,token:env.ADAOS_E2E_HUB_TOKEN,subnet:env.ADAOS_E2E_SUBNET_ID,webspace:env.ADAOS_E2E_WEBSPACE_ID})
 const page=await context.newPage();const url=new URL(env.ADAOS_E2E_CLIENT_URL)
 for(const[key,value]of Object.entries({intent:'webspace.open',zone:'lo',subnet_id:env.ADAOS_E2E_SUBNET_ID,
  webspace_id:env.ADAOS_E2E_WEBSPACE_ID,space_kind:'development',try_local_hub:'1'}))url.searchParams.set(key,value)
 await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:60000})
 await page.waitForFunction(()=>window.__ADAOS_DEBUG_STATE__?.()?.sync?.materializationReady,null,{timeout:60000})
 const state=await page.evaluate(()=>{
  const debug=window.__ADAOS_DEBUG_STATE__?.()||{}
  return {url:location.href,materialization:debug.sync?.materialization,
    render:debug.sync?.renderMaterialization,webspace:debug.sync?.webspaceId,
    transport:debug.sync?.provider,visibleHosts:[...document.querySelectorAll('ada-page-widget-host, ada-widget')]
     .filter(e=>e.getClientRects().length>0).length}
 })
 await fs.writeFile(path.join(output,'state.json'),JSON.stringify(state,null,2))
 await fs.writeFile(path.join(output,'body.txt'),await page.locator('body').innerText())
 await page.screenshot({path:path.join(output,'screen.png')})
 console.log(JSON.stringify(state))
}finally{await browser.close()}
