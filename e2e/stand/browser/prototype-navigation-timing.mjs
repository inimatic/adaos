// Metadata-only Client boot diagnostics for an owned retained Prototype.
import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'
const env=process.env
const checkpoint=JSON.parse(await fs.readFile(env.ADAOS_E2E_CHECKPOINT,'utf8'))
const scenario=checkpoint.steps.find(item=>item.id==='create')?.output?.scenario_id
const preview=checkpoint.cleanup?.previews?.find(item=>item.scenario_id===scenario)
if(env.ENV_TYPE!=='dev'||!checkpoint.cleanup?.test||!scenario?.startsWith('test_')||!preview?.webspace_id?.startsWith('test_'))throw new Error('Owned DEV Prototype required')
const output=path.resolve(env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output,{recursive:true})
const url=new URL(env.ADAOS_E2E_CLIENT_URL||'http://127.0.0.1:8100/')
for(const [key,value] of Object.entries({intent:'webspace.open',zone:'lo',subnet_id:env.ADAOS_E2E_SUBNET_ID,
    webspace_id:preview.webspace_id,space_kind:'development',expected_scenario_id:scenario,try_local_hub:'1'}))url.searchParams.set(key,value)
const browser=await chromium.launch({headless:true})
const report={scenario,webspace:preview.webspace_id,requests:[],websockets:[],scope:'Read-only app observation; browser registration may update owned test workspace presence; NO tokens, headers, query values or payload content retained'}
try{
    const context=await browser.newContext({viewport:{width:1440,height:1000}})
    await context.addInitScript(({hub,token,subnet,webspace})=>{
        window.__ADAOS_DEBUG__=true;window.__ADAOS_BASE__=hub;window.__ADAOS_TOKEN__=token
        for(const [key,value] of Object.entries({adaos_device_id:`e2e-timing:${webspace}`,adaos_webspace_id:webspace,
            adaos_hub_base:hub,adaos_local_hub_base:hub,adaos_try_local_hub:'1',adaos_hub_token:token,
            adaos_local_subnet_id:subnet,adaos_selected_zone:'lo',adaos_last_used_zone:'lo',adaos_lang:'ru'}))localStorage.setItem(key,value)
        window.__E2E_BOOT_TIMELINE__=[]
        let previous=''
        const probe=setInterval(()=>{
            const sync=window.__ADAOS_DEBUG_STATE__?.()?.sync
            if(!sync)return
            const state={providerConnected:!!sync.providerConnected,providerSynced:!!sync.providerSynced,
                materializationReady:!!sync.materializationReady,currentScenario:sync.materialization?.currentScenario||null}
            const identity=JSON.stringify(state)
            if(identity!==previous){previous=identity;window.__E2E_BOOT_TIMELINE__.push({elapsed_ms:performance.now(),...state})}
            if(state.materializationReady)clearInterval(probe)
        },50)
    },{hub:env.ADAOS_E2E_HUB_URL,token:env.ADAOS_E2E_HUB_TOKEN,subnet:env.ADAOS_E2E_SUBNET_ID,webspace:preview.webspace_id})
    const page=await context.newPage()
    const started=Date.now()
    page.on('requestfinished',request=>{
        const endpoint=new URL(request.url())
        const timing=request.timing()
        report.requests.push({path:endpoint.pathname,plane:endpoint.port==='8777'?'hub':endpoint.port==='8100'?'client':'other',
            method:request.method(),start_ms:timing.startTime-started,duration_ms:timing.responseEnd,
            ttfb_ms:timing.responseStart,response_body_ms:timing.responseEnd-timing.responseStart})
    })
    page.on('websocket',socket=>{
        const record={path:new URL(socket.url()).pathname,opened_ms:Date.now()-started,frames:0,bytes:0}
        report.websockets.push(record)
        socket.on('framereceived',event=>{record.first_frame_ms??=Date.now()-started;record.frames++;record.bytes+=typeof event.payload==='string'?Buffer.byteLength(event.payload):event.payload.length})
    })
    await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:60000})
    report.dom_ready_ms=Date.now()-started
    await page.waitForFunction(expected=>{const sync=window.__ADAOS_DEBUG_STATE__?.()?.sync;return sync?.materializationReady&&sync.materialization?.currentScenario===expected},scenario,{timeout:120000})
    report.target_ready_ms=Date.now()-started
    report.timeline=await page.evaluate(()=>window.__E2E_BOOT_TIMELINE__)
    report.ok=true
}catch(error){report.ok=false;report.error=String(error);process.exitCode=1}
finally{await browser.close();await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({ok:report.ok,target_ready_ms:report.target_ready_ms,requests:report.requests.length,websockets:report.websockets.length}))}
