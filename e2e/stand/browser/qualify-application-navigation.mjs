import {chromium,expect} from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import {dismissApplicationSystemOverlay,revealApplicationTarget} from './application-navigation.mjs'
const output=path.resolve(process.env.ADAOS_E2E_OUTPUT||'')
if(!output.startsWith(path.resolve('e2e/artifacts')+path.sep))throw Error('Explicit E2E artifact output required')
await fs.mkdir(output,{recursive:true})
const browser=await chromium.launch({headless:true}),page=await browser.newPage(),report={checks:[],navigation:[]}
try{
 await page.setContent(`<style>ada-layout-region{display:none}ada-layout-region.is-open{display:block}</style>
 <button class="layout-region-trigger" aria-expanded="false">Details</button><main id="primary">Main</main>
 <ada-layout-region data-region-id="details"><header class="layout-region__header"><h2>Details</h2><button>Close panel</button></header><div id="details">Visible detail</div><div id="conditional" hidden>Hidden by application policy</div></ada-layout-region>
 <div class="component-updates-panel"><div class="component-updates-panel__tools"><button>Close</button></div><button id="accept">Accept</button></div>`)
 await page.evaluate(()=>{
  const region=document.querySelector('ada-layout-region'),trigger=document.querySelector('.layout-region-trigger')
  trigger.onclick=()=>{region.classList.add('is-open');trigger.setAttribute('aria-expanded','true')}
  region.querySelector('button').onclick=()=>{region.classList.remove('is-open');trigger.setAttribute('aria-expanded','false')}
  document.querySelector('.component-updates-panel__tools button').onclick=()=>document.querySelector('.component-updates-panel').remove()
  window.accepts=0;document.querySelector('#accept').onclick=()=>window.accepts++
 })
 await dismissApplicationSystemOverlay(page,report)
 expect(await page.evaluate(()=>window.accepts)).toBe(0);report.checks.push({id:'dismiss-without-accepting',passed:true})
 await revealApplicationTarget(page,page.locator('#details'),report,{layout:'compact',timeout:500})
 await expect(page.locator('#details')).toBeVisible();report.checks.push({id:'open-real-disclosure',passed:true})
 await revealApplicationTarget(page,page.locator('#primary'),report,{layout:'compact',timeout:500})
 await expect(page.locator('ada-layout-region.is-open')).toHaveCount(0);report.checks.push({id:'return-to-primary',passed:true})
 let refused=false
 try{await revealApplicationTarget(page,page.locator('#conditional'),report,{layout:'compact',timeout:250})}catch{refused=true}
 expect(refused).toBe(true);await expect(page.locator('#conditional')).not.toBeVisible()
 report.checks.push({id:'conditional-hidden-is-not-forced-visible',passed:true})
 report.passed=true
}finally{
 report.browser=browser.version();await fs.writeFile(path.join(output,'receipt.json'),JSON.stringify(report,null,2));await browser.close()
}
console.log(JSON.stringify({passed:report.passed,checks:report.checks.length}))
