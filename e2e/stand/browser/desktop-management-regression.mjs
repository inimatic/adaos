// Debug Angular client + isolated webspace-session-core.py fixture.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from '@playwright/test'
const app=process.env.ADAOS_WSA_APP_URL||'http://127.0.0.1:8101/'
const core=process.env.ADAOS_WSA_CORE_URL||'http://127.0.0.1:8789'
const artifacts=new URL('../../artifacts/desktop-management/',import.meta.url)
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--remote-debugging-port=0']})
const checks=[],errors=[],loads=new Map(),requests=[]
const api=async(path,body)=>{
  const r=await fetch(core+path,{method:body?'POST':'GET',headers:{'X-AdaOS-Token':'dev-local-token','content-type':'application/json'},...(body?{body:JSON.stringify(body)}:{})})
  assert.equal(r.status,200,path);return r.json()
}
const open=async(desktop,inviteUrl)=>{
  const context=await browser.newContext()
  await context.route(/http:\/\/(127\.0\.0\.1|localhost):(8777|8778|3030)\//,route=>route.abort())
  await context.addInitScript(core=>{window.__ADAOS_BASE__=core;localStorage.setItem('adaos_hub_base',core)},core)
  const page=await context.newPage();loads.set(page,0)
  page.on('request',r=>{if(r.resourceType()==='document')loads.set(page,loads.get(page)+1);requests.push({page:desktop,path:new URL(r.url()).pathname})})
  page.on('pageerror',e=>errors.push(e.message))
  const cdp=await context.newCDPSession(page);await cdp.send('Runtime.enable')
  const url=new URL(inviteUrl||app);url.searchParams.set('debug','1');url.searchParams.set('yjs_webrtc_loopback','1')
  if(!inviteUrl){url.searchParams.set('webspace',desktop);url.searchParams.set('zone','lo')}
  await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:60000})
  if(inviteUrl){
    await page.waitForFunction(()=>ng.getComponent(document.querySelector('app-root'))?.inviteClaim?.preview?.can_accept,null,{timeout:30000})
    await page.evaluate(()=>ng.getComponent(document.querySelector('app-root')).claimCurrentInvite())
  }
  await page.waitForFunction(()=>window.ng?.getComponent(document.querySelector('app-root'))?.ydoc?.getSyncRuntimeSnapshot().providerSynced,null,{timeout:60000})
  await page.evaluate(async()=>{
    const a=ng.getComponent(document.querySelector('app-root'))
    await a.adaos.prepareMemberTransport({webspaceId:a.ydoc.getWebspaceId()})
    window.managementPeer=a.ydoc.channels.rtc.pc;window.managementDocument=document
    window.managementCredential=a.adaos.getDesktopCredential()
  })
  return page
}
const state=page=>page.evaluate(()=>{
  const a=ng.getComponent(document.querySelector('app-root')),y=a.ydoc,s=y.getSyncRuntimeSnapshot()
  const availability=ng.getComponent(document.querySelector('ada-desktop-availability')).sessions.availability$.value
  return {desktop:y.getWebspaceId(),synced:s.providerSynced,live:s.providerLive,state:s.connectionState,
    samePeer:window.managementPeer===y.channels.rtc.pc,sameDocument:window.managementDocument===document,
    availability,credentialPreserved:window.managementCredential===a.adaos.getDesktopCredential(),
    catalog:y.getDesktopCatalog().items.map(item=>({id:item.id,title:item.title})),
    currentApplication:y.toJSON(y.getPath('ui/current_scenario')),channels:y.channels.rtc.desktopChannels.size}
})
const picker=async page=>{
  await page.locator('ada-desktop-picker').first().locator('.desktop-trigger').click()
  return page.locator('.desktop-menu:visible')
}
const choose=async(page,id)=>{
  const menu=await picker(page)
  await menu.locator('input[type=search]').fill(id)
  assert.equal(await menu.locator('.desktop-option').count(),1)
  await menu.locator('.desktop-option').click()
  await page.waitForFunction(id=>ng.getComponent(document.querySelector('app-root')).ydoc.getWebspaceId()===id,id,{timeout:30000})
}
const settings=async page=>{
  const menu=await picker(page)
  await page.waitForFunction(()=>{
    const button=document.querySelector('.desktop-menu .desktop-settings'),box=button?.getBoundingClientRect()
    return box && button.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2))
  })
  await menu.locator('.desktop-settings').click()
  await page.waitForFunction(()=>{
    const el=document.querySelector('ada-desktop-editor');return el&&!ng.getComponent(el).loading
  },null,{timeout:20000})
  return page.locator('ada-desktop-editor')
}
let owner,observer,guest
try {
  owner=await open('wsa-c')
  observer=await open('wsa-a')
  await choose(owner,'wsa-b');await choose(observer,'wsa-b')
  assert.ok((await state(owner)).samePeer&&(await state(owner)).sameDocument)
  checks.push({check:'search-and-switch',samePeer:true})
  let releaseHome
  const homeGate=new Promise(resolve=>{releaseHome=resolve})
  await owner.route('**/api/node/yjs/home-applications?*',async route=>{await homeGate;await route.continue()})
  const editor=await settings(owner)
  assert.equal(await editor.locator('input[tuiinput]').inputValue(),'wsa-b')
  assert.equal(await editor.locator('input[tuiinput]').isEditable(),true)
  assert.equal(await editor.locator('select[aria-label="Home application"]').isDisabled(),true)
  await editor.locator('input[tuiinput]').fill('Draft while Home loads')
  releaseHome()
  await owner.waitForFunction(()=>!ng.getComponent(document.querySelector('ada-desktop-editor')).homeLoading)
  await owner.unroute('**/api/node/yjs/home-applications?*')
  assert.equal(await editor.locator('input[tuiinput]').inputValue(),'Draft while Home loads')
  await editor.getByRole('button',{name:'Cancel',exact:true}).first().click()
  checks.push({check:'discoverable-settings-with-independent-home-loading'})
  assert.equal(await editor.getByRole('button',{name:'Save',exact:true}).count(),0)
  for(const text of ['Home scenario','Soft Reload','Hard Reload','Node:']) assert.ok(!(await editor.innerText()).includes(text))
  await editor.locator('input[tuiinput]').fill('Shared B')
  await editor.getByRole('button',{name:'Save',exact:true}).click()
  await owner.waitForFunction(()=>!ng.getComponent(document.querySelector('ada-desktop-editor')).dirty)
  const current=(await state(owner)).currentApplication
  await editor.locator('select[aria-label="Home application"]').selectOption('core:desktop')
  await editor.getByRole('button',{name:'Save',exact:true}).click()
  await owner.waitForFunction(()=>!ng.getComponent(document.querySelector('ada-desktop-editor')).dirty)
  assert.equal((await state(owner)).currentApplication,current)
  assert.equal((await state(observer)).currentApplication,current)
  checks.push({check:'dirty-form-and-home-without-navigation'})
  await editor.getByRole('button',{name:'Create invitation',exact:true}).click()
  await owner.waitForFunction(()=>ng.getComponent(document.querySelector('ada-desktop-access'))?.link)
  const link=await editor.locator('.invite-link input').inputValue()
  guest=await open('guest',link)
  assert.deepEqual((await state(guest)).catalog.map(item=>item.id),['wsa-b'])
  assert.equal(await guest.locator('ada-desktop-picker .desktop-trigger').first().isDisabled(),true)
  checks.push({check:'scoped-invite-and-single-desktop-label'})
  await api('/fixture/delay/wsa-c?seconds=2',{})
  await owner.evaluate(()=>{
    window.managementSamples=[]
    window.managementTimer=setInterval(()=>{
      const y=ng.getComponent(document.querySelector('app-root')).ydoc,s=y.getSyncRuntimeSnapshot()
      window.managementSamples.push({desktop:y.getWebspaceId(),live:s.providerLive})
    },25)
  })
  await editor.getByRole('button',{name:'Delete desktop',exact:true}).click()
  await editor.getByRole('button',{name:'Delete for everyone',exact:true}).click()
  await owner.waitForFunction(()=>ng.getComponent(document.querySelector('app-root')).ydoc.getWebspaceId()==='wsa-c',null,{timeout:30000})
  await observer.waitForFunction(()=>ng.getComponent(document.querySelector('app-root')).ydoc.getWebspaceId()==='wsa-a',null,{timeout:30000})
  await guest.waitForFunction(()=>ng.getComponent(document.querySelector('ada-desktop-availability')).sessions.availability$.value.state==='empty',null,{timeout:30000})
  await owner.evaluate(()=>clearInterval(window.managementTimer))
  const gap=await owner.evaluate(()=>window.managementSamples.filter(item=>item.desktop==='wsa-b'&&!item.live).length)
  assert.equal(gap,0,'initiator keeps the old document live while preparing its fallback')
  assert.ok((await state(owner)).samePeer&&(await state(observer)).samePeer)
  assert.ok((await state(guest)).credentialPreserved)
  assert.equal((await state(guest)).channels,0)
  assert.ok(!requests.some(item=>item.page==='guest'&&item.path.includes('/webspaces/desktop')))
  checks.push({check:'delete-handoff',initiator:await state(owner),observer:await state(observer),guest:await state(guest),sourceGapSamples:gap})
  for(let attempt=0;attempt<12;attempt++) {
    assert.ok(!(await api('/api/node/yjs/webspaces')).items.some(item=>item.id==='wsa-b'),'deleted identity stays absent')
    await owner.waitForTimeout(250)
  }
  const denied=await fetch(core+'/api/node/yjs/webspaces/wsa-b',{headers:{'X-AdaOS-Token':'dev-local-token'}})
  assert.equal(denied.status,403)
  checks.push({check:'no-resurrection-after-late-recovery'})
  const reopened=await guest.context().newPage()
  // A duplicated browser tab inherits its opener's sessionStorage credential.
  const guestSession=await guest.evaluate(()=>Object.entries(sessionStorage))
  await reopened.addInitScript(entries=>{for(const [key,value] of entries)sessionStorage.setItem(key,value)},guestSession)
  loads.set(reopened,0)
  reopened.on('request',r=>{if(r.resourceType()==='document')loads.set(reopened,loads.get(reopened)+1)})
  reopened.on('pageerror',e=>errors.push(e.message))
  const deletedUrl=new URL(app)
  for(const [key,value] of Object.entries({webspace_id:'wsa-b',zone:'lo',debug:'1',yjs_webrtc_loopback:'1'}))deletedUrl.searchParams.set(key,value)
  await reopened.goto(deletedUrl.href,{waitUntil:'domcontentloaded'})
  await reopened.waitForFunction(()=>ng.getComponent(document.querySelector('ada-desktop-availability'))?.sessions.availability$.value.state==='empty',null,{timeout:30000})
  assert.equal(await reopened.evaluate(()=>ng.getComponent(document.querySelector('app-root')).ydoc.getSyncRuntimeSnapshot().authRequiredReason),null)
  checks.push({check:'reopen-deleted-desktop-without-auth-reset'})
  await owner.waitForFunction(()=>!document.querySelector('ada-scenario-settings-modal'),null,{timeout:10000})
  const menu=await picker(owner)
  await menu.locator('.desktop-add').click()
  const create=menu.locator('ada-desktop-editor')
  await owner.waitForFunction(()=>{const e=document.querySelector('.desktop-menu ada-desktop-editor');return e&&!ng.getComponent(e).loading})
  await create.locator('input[tuiinput]').fill('Created by dropdown')
  assert.equal(await create.locator('input').count(),1,'no internal ID or DEV checkbox in ordinary creation')
  await create.getByRole('button',{name:'Create',exact:true}).click()
  await owner.waitForFunction(()=>ng.getComponent(document.querySelector('app-root')).ydoc.getWebspaceId().startsWith('desktop-'),null,{timeout:30000})
  checks.push({check:'create-from-dropdown',state:await state(owner)})
  await owner.setViewportSize({width:390,height:844})
  const mobileTrigger=owner.locator('ada-desktop-picker .desktop-trigger:visible').first()
  await mobileTrigger.click()
  const mobileMenu=owner.locator('.desktop-menu:visible')
  await mobileMenu.locator('input[type=search]').fill('desktop')
  assert.deepEqual((await mobileMenu.locator('.desktop-option').allTextContents()).map(title=>title.trim()),['desktop'])
  checks.push({check:'filter-visible-titles-without-hidden-id-matches'})
  await mobileMenu.locator('input[type=search]').fill('wsa-a')
  assert.equal(await mobileMenu.locator('.desktop-option').count(),1)
  const bounds=await mobileMenu.boundingBox()
  assert.ok(bounds.x>=0 && bounds.x+bounds.width<=391)
  await owner.keyboard.press('Escape')
  checks.push({check:'mobile-picker-and-keyboard-dismiss'})
  assert.equal(loads.get(owner),1);assert.equal(loads.get(observer),1);assert.equal(loads.get(guest),1)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({ok:true,checks:checks.length,documentLoads:[...loads.values()]}))
} catch(error) {checks.push({check:'failure',error:error.stack});console.error(error);process.exitCode=1}
finally {
  await mkdir(artifacts,{recursive:true})
  await owner?.screenshot({path:new URL('desktop.png',artifacts).pathname.replace(/^\/(?=[A-Z]:)/,'')}).catch(()=>{})
  await guest?.screenshot({path:new URL('guest-empty.png',artifacts).pathname.replace(/^\/(?=[A-Z]:)/,'')}).catch(()=>{})
  await writeFile(new URL('results.json',artifacts),JSON.stringify({checks,errors},null,2))
  await browser.close()
}
