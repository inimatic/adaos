// Read-only acceptance of Management's configured/desired/prepared/active sources.
import {chromium, expect} from '@playwright/test'
import fs from 'node:fs/promises'
import {dismissApplicationSystemOverlay, revealApplicationTarget} from './application-navigation.mjs'

const env = process.env
for (const key of ['ADAOS_E2E_HUB_URL', 'ADAOS_E2E_HUB_TOKEN', 'ADAOS_E2E_SUBNET_ID',
  'ADAOS_E2E_RELEASE_DIGEST', 'ADAOS_E2E_CLIENT_URL', 'ADAOS_E2E_CLIENT_COMMIT', 'ADAOS_E2E_OUTPUT']) {
  if (!env[key]) throw new Error(`Explicit acceptance input required: ${key}`)
}
const output = env.ADAOS_E2E_OUTPUT
await fs.mkdir(output, {recursive:true})
const report = {schema:'adaos.management.delivery_browser.v1', moves_data:false,
  release_digest:env.ADAOS_E2E_RELEASE_DIGEST, samples:[]}
const browser = await chromium.launch({headless:true,
  ...(env.ADAOS_E2E_BROWSER_CHANNEL ? {channel:env.ADAOS_E2E_BROWSER_CHANNEL} : {})})
const redact = value => String(value).replaceAll(env.ADAOS_E2E_HUB_TOKEN, '[redacted]')
try {
  for (const [layout, viewport] of Object.entries({wide:{width:1440,height:1100}, compact:{width:390,height:844}})) {
    const sample = {layout, checks:[], errors:[]}
    report.samples.push(sample)
    const context = await browser.newContext({viewport, serviceWorkers:'block'})
    // Scope the ordinary browser permission to this test origin. Keep CORS,
    // mixed-content rules and local-network checks enabled.
    await context.grantPermissions(['local-network-access'], {origin:new URL(env.ADAOS_E2E_CLIENT_URL).origin})
    await context.addInitScript(({hub,token,subnet}) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_FIXED_RUNTIME_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key,value] of Object.entries({adaos_device_id:'management-delivery-qualification',
        adaos_webspace_id:'desktop', adaos_hub_base:hub, adaos_local_hub_base:hub,
        adaos_try_local_hub:'1', adaos_hub_token:token, adaos_local_subnet_id:subnet,
        adaos_selected_zone:'lo', adaos_last_used_zone:'lo', adaos_env_type:'dev', adaos_lang:'en'})) {
        localStorage.setItem(key,value)
      }
    }, {hub:env.ADAOS_E2E_HUB_URL, token:env.ADAOS_E2E_HUB_TOKEN, subnet:env.ADAOS_E2E_SUBNET_ID})
    const page = await context.newPage()
    page.on('pageerror', error => sample.errors.push(redact(error.message)))
    const host = id => page.locator(`[data-webui-widget-id="${id}"]`).last()
    const command = async (id,button) => {
      await dismissApplicationSystemOverlay(page,sample)
      const target = host(id)
      await revealApplicationTarget(page,target,sample,{layout})
      await expect(target).toBeVisible({timeout:60000})
      await target.locator(`[data-command-id="${button}"]`).click({timeout:45000})
    }
    try {
      const manifestResponse = await context.request.get(new URL('hosting-source.json',env.ADAOS_E2E_CLIENT_URL).href)
      expect(manifestResponse.ok()).toBe(true)
      const manifest = await manifestResponse.json()
      expect(manifest.source_commit).toBe(env.ADAOS_E2E_CLIENT_COMMIT)
      expect(manifest.source_dirty).toBe(false)
      sample.client = manifest
      const url = new URL(env.ADAOS_E2E_CLIENT_URL)
      for (const [key,value] of Object.entries({intent:'webspace.open', zone:'lo',
        subnet_id:env.ADAOS_E2E_SUBNET_ID, webspace_id:'desktop', space_kind:'workspace',
        expected_scenario_id:'web_desktop', adaos_hub_base:env.ADAOS_E2E_HUB_URL,
        try_local_hub:'1', runtime_debug:'1'})) url.searchParams.set(key,value)
      await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:60000})
      for (let attempt=1; attempt<=2; attempt++) {
        if (attempt>1) await page.reload({waitUntil:'domcontentloaded',timeout:60000})
        await page.waitForFunction(digest => {
          const materialization = window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization
          return materialization?.ready && materialization.currentScenario==='web_desktop'
            && materialization.applicationReleaseDigest===digest
        },env.ADAOS_E2E_RELEASE_DIGEST,{timeout:120000})
        const response = await context.request.get(new URL('/api/node/source-policy',env.ADAOS_E2E_HUB_URL).href,
          {headers:{'X-AdaOS-Token':env.ADAOS_E2E_HUB_TOKEN}})
        expect(response.ok()).toBe(true)
        const owner = await response.json()
        expect(owner.ok).toBe(true)
        await command('primary-nav','system')
        const runtimeCard = host('system-update-status-hub')
        await revealApplicationTarget(page,runtimeCard,sample,{layout})
        await expect(runtimeCard).toBeVisible({timeout:60000})
        const ownerVersion = owner.active.version.split('+',1)[0]
        await expect(runtimeCard).toContainText(ownerVersion,{timeout:60000})
        await command('maintenance-navigation','delivery')
        const delivery = host('maintenance-delivery')
        await revealApplicationTarget(page,delivery,sample,{layout})
        await expect(delivery).toBeVisible({timeout:60000})
        for (const label of ['Configured source','Desired release','Prepared slot','Active release',
          'Confirmed readiness','Composition admission','Rollback result']) {
          const heading = delivery.getByText(label,{exact:true})
          await heading.scrollIntoViewIfNeeded({timeout:45000})
          await expect(heading).toBeInViewport({ratio:0.9,timeout:45000})
        }
        for (const label of ['Configured source','Desired release','Prepared slot','Active release',
          owner.policy.branch, owner.active.branch, owner.active.commit, owner.active.version]) {
          await expect(delivery).toContainText(label,{timeout:60000})
        }
        if (!Object.keys(owner.prepared||{}).length) await expect(delivery).toContainText('Not prepared')
        await expect(delivery).toContainText(owner.confirmed_current
          ? 'Confirmed for current runtime' : 'Current runtime not confirmed')
        if (owner.update?.target_rev && owner.update.target_rev!==owner.active.commit) {
          await expect(delivery).not.toContainText(owner.update.target_rev)
        }
        sample.checks.push({attempt, runtime_card_version:ownerVersion, source_policy:owner.policy, active:owner.active,
          prepared:owner.prepared, desired:owner.desired, confirmed:owner.confirmed,
          confirmed_current:owner.confirmed_current, compatibility:owner.compatibility})
      }
      expect(sample.errors).toEqual([])
      await host('maintenance-delivery').screenshot({path:`${output}/${layout}-delivery.png`})
      sample.ok = true
    } catch (error) {
      sample.error = redact(error)
      sample.visible_text = redact(await page.locator('body').innerText()).slice(0,6000)
      await page.screenshot({path:`${output}/${layout}-failure.png`,fullPage:false}).catch(()=>{})
    } finally {
      await context.close()
      await fs.writeFile(`${output}/report.json`,JSON.stringify(report,null,2))
    }
  }
} finally {
  await browser.close()
  report.ok = report.samples.length===2 && report.samples.every(sample=>sample.ok)
  await fs.writeFile(`${output}/report.json`,JSON.stringify(report,null,2))
}
console.log(JSON.stringify({ok:report.ok,samples:report.samples.map(({layout,ok,error})=>({layout,ok,error}))}))
if (!report.ok) process.exitCode = 1
