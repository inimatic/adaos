// Read-only warm Client observer, armed BEFORE publishing an owned target.
import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
const env = process.env
if (env.ENV_TYPE !== 'dev' || !env.ADAOS_E2E_SCENARIO_ID?.startsWith('test_')) throw new Error('Owned DEV target required')
const output = path.resolve(env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const webspace = env.ADAOS_E2E_WEBSPACE_ID
  await context.addInitScript(({ hub, token, subnet, webspace }) => {
    window.__ADAOS_DEBUG__ = true; window.__ADAOS_BASE__ = hub; window.__ADAOS_TOKEN__ = token
    for (const [key, value] of Object.entries({ adaos_webspace_id: webspace, adaos_hub_base: hub,
      adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
      adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
  }, { hub: env.ADAOS_E2E_HUB_URL, token: env.ADAOS_E2E_HUB_TOKEN, subnet: env.ADAOS_E2E_SUBNET_ID, webspace })
  const url = new URL(env.ADAOS_E2E_CLIENT_URL)
  for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: env.ADAOS_E2E_SUBNET_ID,
    webspace_id: webspace, space_kind: 'development', try_local_hub: '1' })) url.searchParams.set(key, value)
  const page = await context.newPage()
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  await page.waitForFunction(() => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materializationReady, null, { timeout: 60_000 })
  const armedAt = Date.now()
  await fs.writeFile(path.join(output, 'armed.json'), JSON.stringify({ armed_at_epoch_ms: armedAt }))
  // Normal browser routing pins its current scenario after initial startup.
  // A shared Preview publication does not authorize replacing that tab's route.
  // Measure a warm-context navigation after the publication receipt instead.
  const ready=path.join(output,'publication-ready.json')
  const deadline=Date.now()+120_000
  while(Date.now()<deadline && !await fs.stat(ready).then(()=>true,()=>false))await delay(100)
  if(Date.now()>=deadline)throw new Error('No owned publication receipt')
  url.searchParams.set('expected_scenario_id',env.ADAOS_E2E_SCENARIO_ID)
  const navigationAt=Date.now()
  await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:60_000})
  const domReadyAt=Date.now()
  await page.waitForFunction(scenario => {
    const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
    return sync?.materializationReady && sync?.materialization?.currentScenario === scenario
      && [...document.querySelectorAll('ada-page-widget-host, ada-widget')].some(element => element.getClientRects().length > 0)
  }, env.ADAOS_E2E_SCENARIO_ID, { timeout: 120_000 })
  const targetReadyAt=Date.now()
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  const receipt = { ok: true, armed_at_epoch_ms: armedAt, visible_at_epoch_ms: Date.now(),
    navigation_started_epoch_ms:navigationAt,scenario_id: env.ADAOS_E2E_SCENARIO_ID,
    dom_ready_epoch_ms:domReadyAt,target_ready_epoch_ms:targetReadyAt,
    phases_seconds:{navigation_to_dom:(domReadyAt-navigationAt)/1000,
      dom_to_target:(targetReadyAt-domReadyAt)/1000,fonts_and_frames:(Date.now()-targetReadyAt)/1000},
    scope: 'warm browser-context navigation AFTER Hub publication: target identity, visible hosts, fonts and two frames; NOT automatic shared-tab switching, all async data or acceptance' }
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(receipt, null, 2))
} catch (error) {
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ ok: false, detail: String(error) }, null, 2))
  process.exitCode = 1
} finally { await browser.close() }
