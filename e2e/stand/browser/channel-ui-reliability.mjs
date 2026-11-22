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
const networkFailures = []
const recordCheck = check => {
  checks.push(check)
  console.log(JSON.stringify({check:check.check, recoveryMs:check.recoveryMs, durationMs:check.durationMs}))
}
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
  page.on('requestfailed',request => networkFailures.push({
    path:new URL(request.url()).pathname,error:request.failure()?.errorText,
  }))
  page.on('request',request => {if(request.resourceType()==='document') loads++})
  const url = new URL(appUrl)
  for (const [key,value] of Object.entries({zone:'lo',webspace:'wsa-a',debug:'1',runtime_debug:'1',yjs_webrtc_loopback:'1'})) url.searchParams.set(key,value)
  const openedAt = Date.now()
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
  const candidateReceipt = () => page.evaluate(() => window.__ADAOS_RUNTIME_DEBUG__.get()
    .filter(event => event.kind==='yjs.provider_candidate.stability.ok').at(-1)?.details)
  const proof = await candidateReceipt()
  assert.equal(proof?.mode,'fresh_round_trip')
  assert.ok(proof.duration_ms < 12000,'confirmation replaces the old twelve-second passive hold')
  recordCheck({check:'full client first RTC sync with GC regression',state:await snapshot(),command:directCommand,
    navigationToRtcMs:Date.now()-openedAt,candidateProof:proof})

  const reuse = await page.evaluate(async () => {
    const a=ng.getComponent(document.querySelector('app-root')),rtc=a.ydoc.channels.rtc
    const peer=rtc.pc,generation=rtc.peerGenerationId,started=performance.now()
    const result=await a.adaos.prepareMemberTransport({webspaceId:'wsa-a'})
    return {direct:result.direct,samePeer:rtc.pc===peer,sameGeneration:rtc.peerGenerationId===generation,
      durationMs:performance.now()-started}
  })
  assert.equal(reuse.direct,true)
  assert.equal(reuse.samePeer,true)
  assert.equal(reuse.sameGeneration,true)
  recordCheck({check:'repeated preparation reuses the verified live peer without SDP or ICE restart',...reuse})

  // An available RTC peer can still be in the existing route warmup hold.
  // Exercise a timeout only after both affected semantic channels selected RTC.
  await page.waitForFunction(() => {
    const state=ng.getComponent(document.querySelector('app-root')).ydoc.channels.getSnapshot()
    return state.channels.command.activePath==='webrtc_data:events' && state.channels.event.activePath==='webrtc_data:events'
  },null,{timeout:30000})
  const wsTimeout = await page.evaluate(async () => {
    const app=ng.getComponent(document.querySelector('app-root')),channels=app.ydoc.channels,rtc=channels.rtc
    const peer=rtc.pc,generation=rtc.peerGenerationId,started=performance.now()
    const handle=channels.handleIncomingControlMessage
    // Admit one real navigation command, then drop only its ACK at the browser.
    // The command is not retried; its uncertain outcome must not penalize RTC.
    channels.handleIncomingControlMessage=function(raw) {
      try {const msg=JSON.parse(raw); if(msg.t==='ack' && String(msg.id).startsWith('desktop.webspace.use.'))
        return {handled:true}} catch {}
      return handle.call(this,raw)
    }
    let timedOut=false
    try {await app.adaos.sendEventsCommand('desktop.webspace.use',{webspace_id:'wsa-a'},500)}
    catch(error) {timedOut=String(error).includes('timeout')}
    finally {channels.handleIncomingControlMessage=handle}
    const failed=window.__ADAOS_RUNTIME_DEBUG__.get().filter(event => event.kind==='control_command.failed' &&
      event.details?.kind==='desktop.webspace.use').at(-1)
    const state=channels.getSnapshot()
    return {timedOut,path:failed?.details?.path,command:state.channels.command.activePath,
      event:state.channels.event.activePath,samePeer:rtc.pc===peer,sameGeneration:rtc.peerGenerationId===generation,
      durationMs:performance.now()-started}
  })
  recordCheck({check:'lost navigation ACK is attributed to its actual WS transport and preserves healthy RTC',...wsTimeout})
  assert.equal(wsTimeout.timedOut,true)
  assert.equal(wsTimeout.path,'ws')
  assert.equal(wsTimeout.command,'webrtc_data:events')
  assert.equal(wsTimeout.event,'webrtc_data:events')
  assert.equal(wsTimeout.samePeer,true)
  assert.equal(wsTimeout.sameGeneration,true)

  const scopedClosedAt=Date.now()
  await page.evaluate(() => {
    const app=ng.getComponent(document.querySelector('app-root')),rtc=app.ydoc.channels.rtc
    window.__channelRecoveryPeer={peer:rtc.pc,generation:rtc.peerGenerationId}
    const channel=rtc.getYjsChannel('wsa-a')
    if(!channel || channel.readyState!=='open') throw new Error('no live scoped Yjs channel to close')
    channel.close()
  })
  await page.waitForFunction(() => {
    const s=ng.getComponent(document.querySelector('app-root')).ydoc.getSyncRuntimeSnapshot()
    return s.currentPath==='yws' && s.providerSynced && s.syncPathUpgrade.retryAt
  },null,{timeout:15000})
  const cooldownState=await snapshot()
  const nonce=`closed-channel-${crypto.randomUUID()}`
  await api('/fixture/probe/wsa-a?value='+nonce,{})
  await page.waitForFunction(value => ng.getComponent(document.querySelector('app-root')).ydoc.doc.getMap('data').get('durable_probe')===value,
    nonce,{timeout:10000})
  await page.waitForFunction(() => {
    const s=ng.getComponent(document.querySelector('app-root')).ydoc.getSyncRuntimeSnapshot()
    return s.currentPath==='webrtc_data:yjs' && s.providerSynced
  },null,{timeout:75000})
  const scopedRecovery=await page.evaluate(() => {
    const app=ng.getComponent(document.querySelector('app-root')),rtc=app.ydoc.channels.rtc,saved=window.__channelRecoveryPeer
    return {samePeer:rtc.pc===saved.peer,sameGeneration:rtc.peerGenerationId===saved.generation}
  })
  assert.equal(scopedRecovery.samePeer,true)
  assert.equal(scopedRecovery.sameGeneration,true)
  await api('/fixture/probe/wsa-a?value='+nonce+'-fresh',{})
  await page.waitForFunction(value => ng.getComponent(document.querySelector('app-root')).ydoc.doc.getMap('data').get('durable_probe')===value,
    nonce+'-fresh',{timeout:10000})
  assert.equal((await candidateReceipt())?.mode,'fresh_round_trip')
  recordCheck({check:'closed scoped Yjs channel returns from live YWS on the same peer after the unchanged cooldown',
    cooldownState,state:await snapshot(),recoveryMs:Date.now()-scopedClosedAt,...scopedRecovery})

  const refreshedAt=Date.now()
  await page.reload({waitUntil:'domcontentloaded'})
  await page.waitForFunction(() => {
    const s=window.ng?.getComponent(document.querySelector('app-root'))?.ydoc?.getSyncRuntimeSnapshot()
    return s?.providerSynced && s.currentPath==='webrtc_data:yjs'
  },null,{timeout:45000})
  const refreshedProof=await candidateReceipt()
  assert.equal(refreshedProof?.mode,'fresh_round_trip')
  assert.ok(refreshedProof.duration_ms < 12000)
  recordCheck({check:'page refresh establishes a new connection with a fresh proof',
    refreshToRtcMs:Date.now()-refreshedAt,candidateProof:refreshedProof,state:await snapshot()})

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
  recordCheck({check:'silent Yjs stall recovers automatically while commands stay direct',state:await snapshot(),recoveryMs:stallRecoveryMs,command:stillDirect})

  const outageStarted=Date.now()
  await api('/fixture/close-rtc',{})
  await api('/fixture/probe/wsa-a?value=during-rtc-outage',{})
  await page.waitForFunction(() => {
    const y=ng.getComponent(document.querySelector('app-root')).ydoc,s=y.getSyncRuntimeSnapshot()
    return s.providerSynced && y.doc.getMap('data').get('durable_probe')==='during-rtc-outage'
  },null,{timeout:45000})
  recordCheck({check:'remote physical peer failure recovers automatically',state:await snapshot(),recoveryMs:Date.now()-outageStarted,command:await commandProbe()})

  // Keep RTC unavailable through the same cooldown used by the shipped client.
  await page.evaluate(() => ng.getComponent(document.querySelector('app-root')).ydoc.channels.suppressWebRtcYjsSync('qualification',30000))
  await api('/fixture/close-rtc',{})
  await api('/fixture/probe/wsa-a?value=forced-yws',{})
  await page.waitForFunction(() => {
    const y=ng.getComponent(document.querySelector('app-root')).ydoc,s=y.getSyncRuntimeSnapshot()
    return s.providerSynced && s.currentPath==='yws' && y.doc.getMap('data').get('durable_probe')==='forced-yws'
  },null,{timeout:45000})
  recordCheck({check:'automatic YWS fallback while RTC is suppressed',state:await snapshot()})
  assert.equal(loads,2)
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
  await writeFile(path.join(artifacts,'result.json'),JSON.stringify({checks,errors,networkFailures,loads},null,2)+'\n')
  await browser.close()
}
