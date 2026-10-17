import { expect } from '@playwright/test'

export async function dismissApplicationSystemOverlay(page, sample, {waitMs=0, timeout=15000}={}) {
  const panel=page.locator('.component-updates-panel').filter({visible:true}).first()
  if(waitMs) await panel.waitFor({state:'visible',timeout:waitMs}).catch(()=>{})
  if(!await panel.count())return
  await panel.locator('.component-updates-panel__tools button').last().click({timeout})
  await panel.waitFor({state:'hidden',timeout})
  ;(sample.navigation??=[]).push({kind:'system-overlay-dismissed',overlay:'component-updates'})
}

export async function revealApplicationTarget(page,target,sample,{layout,timeout=15000}={}) {
  if(layout!=='compact'||!await target.count())return
  const info=await target.evaluate(node=>{
    const region=node.closest('ada-layout-region')
    return {region:region?.getAttribute('data-region-id'),modal:!!node.closest('ion-modal'),
      label:region?.querySelector('.layout-region__header h2')?.textContent?.trim()}
  })
  const opened=page.locator('ada-layout-region.is-open').last()
  if(await opened.count()&&!info.modal&&info.region!==await opened.getAttribute('data-region-id')) {
    await opened.locator('.layout-region__header button').first().click({timeout})
    await expect(opened).toHaveCount(0,{timeout})
    ;(sample.navigation??=[]).push({kind:'compact-region-closed'})
  }
  if(!await target.isVisible()&&info.region&&info.label) {
    const triggers=page.locator('.layout-region-trigger')
    const escaped=info.label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')
    const trigger=triggers.filter({hasText:new RegExp('^\\s*'+escaped+'\\s*$')})
    await expect(trigger).toHaveCount(1,{timeout})
    await trigger.click({timeout})
    await expect(target).toBeVisible({timeout})
    ;(sample.navigation??=[]).push({kind:'compact-region-opened',region:info.region,label:info.label})
  }
}
