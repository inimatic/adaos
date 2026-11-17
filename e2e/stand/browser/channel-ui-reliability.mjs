// Requires a debug client and the isolated WSA fixture with --gc-origin-regression.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from '@playwright/test'
import path from 'node:path'

const appUrl = process.env.ADAOS_WSA_APP_URL || 'http://127.0.0.1:8100/'
const core = process.env.ADAOS_WSA_CORE_URL || 'http://127.0.0.1:8789'
const artifacts = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'e2e/artifacts/channel-ui-reliability')
await mkdir(artifacts, {recursive:true})
const browser = await chromium.launch({headless:true, channel:'chrome'})
const checks = [], errors = []
let loads = 0, page
const api = async (endpoint, body) => {
  const response = await fetch(core + endpoint, {method:body ? 'POST' : 'GET',
    headers:{'X-AdaOS-Token':'dev-local-token','Content-Type':'application/json'},
    ...(body ? {body:JSON.stringify(body)} : {})})
  assert.equal(response.status,200,response.status === 200 ? endpoint : await response.text())
}
const json = async endpoint => {
  const response = await fetch(core + endpoint, {headers:{'X-AdaOS-Token':'dev-local-token'}})
  assert.equal(response.status,200,endpoint)
  return response.json()
}
try {
  const context = await browser.newContext()
  await context.route(/http:\/\/(127\.0\.0\.1|localhost):(8777|8778|3030)\//, route => route.abort())
  await context.addInitScript(core => {
    window.__ADAOS_BASE__ = core
    localStorage.setItem('adaos_hub_base',core)
    localStorage.setItem('adaos_device_id','channel-ui-fixture-device')
  },core)
  page = await context.newPage()
  page.on('pageerror',error => errors.push(error.message))
  page.on('request',request => {if(request.resourceType()==='document') loads++})
  const url = new URL(appUrl)
  for (const [key,value] of Object.entries({zone:'lo',webspace:'wsa-a',debug:'1',yjs_webrtc_loopback:'1'})) url.searchParams.set(key,value)
  await page.goto(url.href,{waitUntil:'domcontentloaded'})
  await page.waitForFunction(() => window.ng?.getComponent(document.querySelector('app-root'))?.ydoc?.getSyncRuntimeSnapshot().providerSynced,null,{timeout:60000})
  const snapshot = () => page.evaluate(() => {
    const a=ng.getComponent(document.querySelector('app-root')),y=a.ydoc,s=y.getSyncRuntimeSnapshot()
    return {path:s.currentPath,synced:s.providerSynced,live:s.providerLive,
      proof:s.dataChannelProof,
      probe:y.doc.getMap('data').get('durable_probe'),providers:y.trackedSyncProviders.size,
      cooldown:s.ywsGuardCooldownReason,rtc:y.channels.rtc.getRecoverySnapshot()}
  })
  const commandProbe = async () => {
    const receipt = await page.evaluate(async () => {
      const a=ng.getComponent(document.querySelector('app-root')),started=performance.now()
      const direct=a.adaos.canSendDirectEvents()
      const ack=await a.adaos.sendEventsCommand('platform.notifications.get',{webspace_id:'wsa-a'},5000)
      return {acknowledged:ack != null && ack.ok !== false,direct,ms:performance.now()-started}
    })
    assert.equal(receipt.acknowledged,true)
    return receipt
  }
  await page.evaluate(async() => {
    const a=ng.getComponent(document.querySelector('app-root'))
    await a.adaos.prepareMemberTransport({webspaceId:'wsa-a'})
  })
  await page.waitForFunction(() => {
    const s=ng.getComponent(document.querySelector('app-root')).ydoc.getSyncRuntimeSnapshot()
    return s.providerSynced && s.currentPath==='webrtc_data:yjs'
  },null,{timeout:45000})
  await api('/fixture/probe/wsa-a?value=first-rtc',{})
  await page.waitForFunction(() => ng.getComponent(document.querySelector('app-root')).ydoc.doc.getMap('data').get('durable_probe')==='first-rtc')
  const directCommand = await commandProbe()
  assert.equal(directCommand.direct,true)
  checks.push({check:'full client first RTC sync with GC regression',state:await snapshot(),command:directCommand})

  const stallStarted=Date.now()
  await api('/fixture/stall-yjs',{})
  await api('/fixture/probe/wsa-a?value=during-silent-stall',{})
  await page.waitForFunction(() => {
    const y=ng.getComponent(document.querySelector('app-root')).ydoc,s=y.getSyncRuntimeSnapshot()
    return s.providerSynced && s.currentPath==='yws' && y.doc.getMap('data').get('durable_probe')==='during-silent-stall'
  },null,{timeout:35000})
  const stallRecoveryMs=Date.now()-stallStarted
  assert.ok(stallRecoveryMs < 30000)
  const stillDirect=await commandProbe()
  assert.equal(stillDirect.direct,true,'Yjs recovery preserves the independent RTC command channel')
  checks.push({check:'silent Yjs stall recovers automatically while commands stay direct',state:await snapshot(),recoveryMs:stallRecoveryMs,command:stillDirect})

  const outageStarted=Date.now()
  await api('/fixture/close-rtc',{})
  await api('/fixture/probe/wsa-a?value=during-rtc-outage',{})
  await page.waitForFunction(() => {
    const y=ng.getComponent(document.querySelector('app-root')).ydoc,s=y.getSyncRuntimeSnapshot()
    return s.providerSynced && y.doc.getMap('data').get('durable_probe')==='during-rtc-outage'
  },null,{timeout:45000})
  checks.push({check:'remote physical peer failure recovers automatically',state:await snapshot(),recoveryMs:Date.now()-outageStarted,command:await commandProbe()})

  // Keep RTC unavailable through the same cooldown used by the shipped client.
  await page.evaluate(() => ng.getComponent(document.querySelector('app-root')).ydoc.channels.suppressWebRtcYjsSync('qualification',30000))
  await api('/fixture/close-rtc',{})
  await api('/fixture/probe/wsa-a?value=forced-yws',{})
  await page.waitForFunction(() => {
    const y=ng.getComponent(document.querySelector('app-root')).ydoc,s=y.getSyncRuntimeSnapshot()
    return s.providerSynced && s.currentPath==='yws' && y.doc.getMap('data').get('durable_probe')==='forced-yws'
  },null,{timeout:45000})
  checks.push({check:'automatic YWS fallback while RTC is suppressed',state:await snapshot()})
  assert.equal(loads,1)
  assert.deepEqual(errors,[])
  assert.equal((await snapshot()).providers,1)
  assert.equal((await json('/fixture/state')).rooms['wsa-a'].durable_probe,'forced-yws')
  console.log(JSON.stringify({result:'passed',checks:checks.length,loads}))
} catch(error) {
  checks.push({check:'failure',error:String(error.stack),state:await page?.evaluate(() => {
    const a=window.ng?.getComponent(document.querySelector('app-root'))
    return {base:a?.adaos?.getBaseUrl(),sync:a?.ydoc?.getSyncRuntimeSnapshot(),body:document.body.innerText.slice(0,1500)}
  }).catch(()=>null)})
  throw error
} finally {
  await writeFile(path.join(artifacts,'result.json'),JSON.stringify({checks,errors,loads},null,2)+'\n')
  await browser.close()
}
