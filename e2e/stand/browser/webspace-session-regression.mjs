// Requires a debug Angular build and the isolated webspace-session-core.py fixture.
// Start both from the repository root; commands are in the implementation record.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from '@playwright/test'

const appUrl = process.env.ADAOS_WSA_APP_URL || 'http://127.0.0.1:8101/'
const core = process.env.ADAOS_WSA_CORE_URL || 'http://127.0.0.1:8789'
const artifacts = new URL('../../artifacts/webspace-session/', import.meta.url)
const browser = await chromium.launch({ headless: true,
  ...(process.env.ADAOS_CHROME_PATH ? { executablePath: process.env.ADAOS_CHROME_PATH } : { channel: 'chrome' }),
  args: ['--remote-debugging-port=0'] })
const checks = [], errors = [], loads = new Map()
const api = async (path, body) => {
  const response = await fetch(core + path, { method: body ? 'POST' : 'GET',
    headers: { 'X-AdaOS-Token': 'dev-local-token', 'content-type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}) })
  assert.equal(response.status, 200, path)
  return response.json()
}
const open = async (desktop, inviteId) => {
  const context = await browser.newContext()
  // An isolated fixture must never discover or mutate the developer's Core.
  await context.route(/http:\/\/(127\.0\.0\.1|localhost):(8777|8778|3030)\//, route => route.abort())
  await context.addInitScript(({core, inviteId}) => {
    window.__ADAOS_BASE__ = core
    localStorage.setItem('adaos_hub_base', core)
    localStorage.setItem('adaos_device_id', 'wsa-same-device-across-pages')
    if (inviteId) sessionStorage.setItem('adaos_invite_pending', JSON.stringify({inviteId,
      subnetId:'wsa-subnet', hubId:'wsa-subnet', hubBase:core, zone:'lo',
      scope:{kind:'webspace',id:'wsa-b'}, at:Date.now()}))
  }, {core, inviteId})
  const page = await context.newPage()
  loads.set(page, 0)
  page.on('request', request => { if(request.resourceType() === 'document') loads.set(page, loads.get(page) + 1) })
  page.on('pageerror', error => errors.push(error.message))
  const cdp = await context.newCDPSession(page)
  await cdp.send('Runtime.enable')
  const url = new URL(appUrl)
  for (const [key,value] of Object.entries({zone:'lo', webspace:desktop, debug:'1', yjs_webrtc_loopback:'1'})) url.searchParams.set(key,value)
  if (inviteId) {
    url.searchParams.set('adaos_invite',inviteId)
    url.searchParams.set('adaos_hub_base',core)
    url.searchParams.set('subnet_id','wsa-subnet')
  }
  await page.goto(url.href, {waitUntil:'domcontentloaded'})
  if (inviteId) {
    try {
      await page.waitForFunction(() => window.ng?.getComponent(document.querySelector('app-root'))?.inviteClaim?.preview?.can_accept, null, {timeout:30000})
    } catch (error) {
      checks.push({check:'invite-preview-failed',state:await page.evaluate(()=>{
        const a=ng.getComponent(document.querySelector('app-root'))
        return {base:a.adaos.getBaseUrl(),message:a.inviteClaim.message,
          preview:{scope:a.inviteClaim.preview?.scope,status:a.inviteClaim.preview?.status,can_accept:a.inviteClaim.preview?.can_accept},
          desktop:a.ydoc.getWebspaceId(),claimBusy:a.inviteClaim.busy,joinOpen:a.joinPanelOpen}
      })})
      throw error
    }
    await page.evaluate(() => ng.getComponent(document.querySelector('app-root')).claimCurrentInvite())
  }
  await page.waitForFunction(() => window.ng?.getComponent(document.querySelector('app-root'))?.ydoc?.getSyncRuntimeSnapshot().providerSynced, null, {timeout:60000})
  assert.ok(await page.evaluate(()=>{
    const a=ng.getComponent(document.querySelector('app-root'))
    return a.adaos.browserPageId===a.ydoc.getBrowserPageId()
  }), 'page identity is bound to the control transport')
  return page
}
const state = page => page.evaluate(() => {
  const a=ng.getComponent(document.querySelector('app-root')), y=a.ydoc, rtc=y.channels.rtc
  return {desktop:y.getWebspaceId(), path:y.getSyncRuntimeSnapshot().currentPath,
    synced:y.getSyncRuntimeSnapshot().providerSynced, live:y.getSyncRuntimeSnapshot().providerLive,
    generation:rtc.peerGenerationId, samePeer:!window.wsaPeer||window.wsaPeer===rtc.pc,
    sameDtls:!window.wsaDtls||window.wsaDtls===rtc.pc?.sctp?.transport,
    sameIce:!window.wsaUfrag||window.wsaUfrag===rtc.pc?.localDescription?.sdp.match(/a=ice-ufrag:(\S+)/)?.[1],
    sameMedia:!window.wsaMedia||window.wsaMedia===rtc.mediaChannel,
    sameDocument:!window.wsaDocument||window.wsaDocument===document,
    providers:y.trackedSyncProviders.size, channels:rtc.desktopChannels.size,
    peer:rtc.getRecoverySnapshot(), fixture:y.toJSON(y.getPath('data/fixture')),
    nav:y.toJSON(y.getPath('ui/navigation')), fullCatalog:y.toJSON(y.getPath('data/webspaces')),
    catalog:y.getDesktopCatalog(), historyLength:history.length,
    pageState:ng.getComponent(document.querySelector('ada-desktop'))?.pageState?.getSnapshot()}
})
const switchTo = async (page, desktop) => {
  const before=Date.now()
  const sourceDestroyed=await page.evaluate(async desktop => {
    const y=ng.getComponent(document.querySelector('app-root')).ydoc, old=y.doc
    await y.switchWebspace(desktop)
    return old===y.doc || old.isDestroyed
  },desktop)
  const result=await state(page)
  assert.equal(result.desktop,desktop); assert.equal(result.fixture.desktop,desktop)
  assert.equal(result.providers,1); assert.ok(sourceDestroyed)
  assert.ok(result.samePeer && result.sameDocument)
  checks.push({check:'switch',desktop,ms:Date.now()-before,...result})
  return result
}
let owner, visitor, observer
try {
  owner=await open('wsa-a')
  await owner.evaluate(async()=>{
    const a=ng.getComponent(document.querySelector('app-root'))
    await a.adaos.prepareMemberTransport({webspaceId:'wsa-a'})
    window.wsaPeer=a.ydoc.channels.rtc.pc; window.wsaDocument=document
    window.wsaDtls=window.wsaPeer.sctp.transport
    window.wsaMedia=a.ydoc.channels.rtc.mediaChannel
    window.wsaUfrag=window.wsaPeer.localDescription.sdp.match(/a=ice-ufrag:(\S+)/)?.[1]
    a.ydoc.doc.getMap('data').set('source_only','a')
  })
  const initial=await state(owner)
  for(const target of ['wsa-b','wsa-a','wsa-b']) {
    const result=await switchTo(owner,target)
    assert.equal(result.path,'webrtc_data:yjs');assert.equal(result.generation,initial.generation)
    assert.equal(result.channels,1);assert.equal(result.peer.mediaChannelState,'open')
    assert.ok(result.sameDtls&&result.sameIce&&result.sameMedia)
    if(target==='wsa-b') assert.equal(await owner.evaluate(()=>ng.getComponent(document.querySelector('app-root')).ydoc.doc.getMap('data').get('source_only')),undefined)
  }
  await owner.goBack()
  await owner.waitForFunction(()=>ng.getComponent(document.querySelector('app-root')).ydoc.getWebspaceId()==='wsa-a',null,{timeout:35000})
  await owner.waitForFunction(()=>!ng.getComponent(document.querySelector('app-root')).browserHistoryScenarioSwitch)
  const historyLength=(await state(owner)).historyLength
  await owner.goForward()
  await owner.waitForFunction(()=>ng.getComponent(document.querySelector('app-root')).ydoc.getWebspaceId()==='wsa-b',null,{timeout:35000})
  assert.equal((await state(owner)).historyLength,historyLength)
  checks.push({check:'back-forward',samePeer:(await state(owner)).samePeer})
  const rapid=await owner.evaluate(async()=>{
    const y=ng.getComponent(document.querySelector('app-root')).ydoc
    return Promise.allSettled([y.switchWebspace('wsa-a'), y.switchWebspace('wsa-c')])
  })
  assert.equal(rapid[0].status,'rejected');assert.equal((await state(owner)).desktop,'wsa-c')
  checks.push({check:'rapid-supersession',providers:(await state(owner)).providers})
  await api('/fixture/delay/wsa-b?seconds=2',{})
  const independent=await owner.evaluate(async()=>{
    const a=ng.getComponent(document.querySelector('app-root'))
    const switching=a.ydoc.switchWebspace('wsa-b')
    const at=performance.now()
    const ack=await a.adaos.sendEventsCommand('desktop.navigation.set',{webspace_id:'wsa-c',scenario_id:'wsa-fixture',state:{activeTab:'control'}})
    const ms=performance.now()-at
    await switching
    return {ms,ok:ack.ok}
  })
  assert.ok(independent.ok && independent.ms<1500)
  await api('/fixture/delay/wsa-b?seconds=0',{})
  checks.push({check:'control-during-setup',...independent})
  for(const target of ['wsa-a','wsa-b','wsa-a','wsa-b','wsa-a']) await switchTo(owner,target)
  const invite=await api('/fixture/visitor',{})
  visitor=await open('wsa-b',invite.invite_id)
  await visitor.evaluate(()=>ng.getComponent(document.querySelector('app-root')).ydoc.refreshDesktopCatalog())
  const admitted=await state(visitor)
  assert.deepEqual(admitted.catalog.items.map(item=>item.id),['wsa-b'])
  assert.equal(admitted.catalog.can_create,false)
  const denied=await visitor.evaluate(async()=>{
    const a=ng.getComponent(document.querySelector('app-root')), old=a.ydoc.doc, url=location.href
    let error;try{await a.ydoc.switchWebspace('wsa-a')}catch(e){error=e.message}
    const response=await fetch(a.adaos.getBaseUrl()+'/api/node/yjs/webspaces/wsa-b',{
      method:'PATCH',headers:{...a.adaos.getAuthHeaders(),'content-type':'application/json'},body:JSON.stringify({display_name:'forged'})})
    return {error,sourceSame:old===a.ydoc.doc,urlSame:url===location.href,systemStatus:response.status}
  })
  assert.ok(denied.error&&denied.sourceSame&&denied.urlSame);assert.equal(denied.systemStatus,403)
  checks.push({check:'invitation-and-system-denial',...denied})
  observer=await open('wsa-b')
  for(const scenario of ['wsa-second','wsa-fixture']) {
    await visitor.evaluate(scenario=>ng.getComponent(document.querySelector('app-root')).adaos.sendEventsCommand(
      'desktop.scenario.set',{webspace_id:'wsa-b',scenario_id:scenario}),scenario)
    for(const page of [visitor,observer]) await page.waitForFunction(scenario=>{
      const y=ng.getComponent(document.querySelector('app-root')).ydoc
      return y.toJSON(y.getPath('ui/current_scenario'))===scenario
    },scenario)
    assert.equal(await owner.evaluate(()=>ng.getComponent(document.querySelector('app-root')).ydoc.toJSON(
      ng.getComponent(document.querySelector('app-root')).ydoc.getPath('ui/current_scenario'))),'wsa-fixture')
  }
  checks.push({check:'shared-application-navigation'})
  await visitor.evaluate(()=>{
    const a=ng.getComponent(document.querySelector('app-root'))
    a.ydoc.doc.getMap('data').set('role_hidden','inspectable')
    return a.adaos.sendEventsCommand('desktop.navigation.set',{webspace_id:'wsa-b',scenario_id:'wsa-fixture',state:{activeTab:'details'}})
  })
  await observer.waitForFunction(()=>{
    const y=ng.getComponent(document.querySelector('app-root')).ydoc
    return y.toJSON(y.getPath('ui/navigation'))?.state?.activeTab==='details' && y.doc.getMap('data').get('role_hidden')==='inspectable'
  })
  assert.notEqual((await state(owner)).nav?.state?.activeTab,'details')
  await observer.waitForFunction(()=>ng.getComponent(document.querySelector('ada-desktop'))?.pageState?.getSnapshot().activeTab==='details')
  for(const page of [visitor,observer]) {
    const s=await state(page)
    assert.ok((s.fullCatalog?.items||[]).every(item=>item.id==='wsa-b'))
  }
  const sharedGeneration=(await state(observer)).nav.generation
  await observer.waitForTimeout(500)
  assert.equal((await state(observer)).nav.generation,sharedGeneration)
  checks.push({check:'shared-page-no-echo',generation:sharedGeneration,observerState:(await state(observer)).pageState})
  const presence=await api('/fixture/state')
  checks.push({check:'simultaneous-presence',snapshot:presence.sessions})
  const pageIds=new Set()
  const collect=value=>{
    if(!value || typeof value!=='object') return
    const identity=value.browser_page_id || value.peer_id || value.client_limit_id || value.browser_session_id
    if(identity) pageIds.add(identity)
    for(const item of Object.values(value)) collect(item)
  }
  collect(presence.sessions)
  assert.ok(pageIds.size>=3,'three simultaneous page identities')
  const sid=await visitor.evaluate(()=>JSON.parse(sessionStorage.getItem('adaos_invite_accepted')).sessionId)
  await api('/api/personalization/admin/sessions/'+sid+'/revoke',{})
  await visitor.waitForFunction(()=>{
    const y=ng.getComponent(document.querySelector('app-root')).ydoc
    return !y.getSyncRuntimeSnapshot().providerLive && y.channels.rtc.desktopChannels.size===0
  },null,{timeout:15000})
  await visitor.evaluate(()=>ng.getComponent(document.querySelector('app-root')).ydoc.refreshDesktopCatalog())
  assert.deepEqual((await state(visitor)).catalog.items,[])
  assert.ok((await state(owner)).synced && (await state(observer)).synced)
  checks.push({check:'revoke-stops-only-affected-attachments'})
  await observer.evaluate(()=>ng.getComponent(document.querySelector('app-root')).ydoc.doc.getMap('data').set('durable_probe','after-last-disconnect'))
  let delivered=false
  for(let attempt=0;attempt<30;attempt++) {
    if((await api('/fixture/state')).rooms['wsa-b']?.durable_probe==='after-last-disconnect') {delivered=true;break}
    await observer.waitForTimeout(100)
  }
  assert.ok(delivered,'browser update reached the live room before disconnect')
  await observer.context().close(); await visitor.context().close()
  const evicted=await api('/fixture/evict/wsa-b',{})
  assert.equal(evicted.ystore_snapshot_persisted,true)
  observer=await open('wsa-b')
  assert.equal(await observer.evaluate(()=>ng.getComponent(document.querySelector('app-root')).ydoc.doc.getMap('data').get('durable_probe')),'after-last-disconnect')
  checks.push({check:'last-disconnect-evict-restore',persisted:true})
  await observer.evaluate(()=>ng.getComponent(document.querySelector('app-root')).adaos.sendEventsCommand('desktop.webspace.delete',{id:'wsa-b'}))
  let listing
  for(let attempt=0;attempt<30;attempt++) {
    listing=await api('/api/node/yjs/webspaces')
    if(!listing.items.some(item=>item.id==='wsa-b')) break
    await observer.waitForTimeout(100)
  }
  assert.ok(!listing.items.some(item=>item.id==='wsa-b'))
  checks.push({check:'explicit-delete'})
  // Independently qualify synchronization fallback in the still-authorized page.
  await owner.evaluate(()=>{
    const y=ng.getComponent(document.querySelector('app-root')).ydoc
    y.channels.suppressWebRtcYjsSync('qualification',30000);y.channels.rtc.close()
    window.wsaPeer=null
  })
  const fallback=await switchTo(owner,'wsa-c')
  assert.equal(fallback.path,'yws')
  checks.push({check:'yws-fallback'})
  assert.equal(loads.get(owner),1);assert.equal(errors.length,0)
  console.log(JSON.stringify({ok:true,checks:checks.length,documentLoads:loads.get(owner)}))
} catch(error) {
  checks.push({check:'failure',error:error.stack})
  process.exitCode=1
  console.error(error)
} finally {
  await mkdir(artifacts,{recursive:true})
  await owner?.screenshot({path:new URL('desktop.png',artifacts).pathname.replace(/^\/(?=[A-Z]:)/,'')}).catch(()=>{})
  await writeFile(new URL('results.json',artifacts),JSON.stringify({checks,errors},null,2))
  await browser.close()
}
