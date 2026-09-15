import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from '@playwright/test'

const { ADAOS_E2E_HUB_URL: hub, ADAOS_E2E_HUB_TOKEN: token,
  ADAOS_E2E_ACTION_ID: actionId, ADAOS_E2E_OUTPUT: output } = process.env
assert.equal(process.env.ENV_TYPE, 'dev')
assert.ok(hub && token && actionId?.startsWith('pa.runtime_action.') && output)
const browser = await chromium.launch({ headless: true })
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ hub, token }) => {
    window.__ADAOS_DEBUG__ = true
    window.__ADAOS_BASE__ = hub
    window.__ADAOS_TOKEN__ = token
    for (const [key, value] of Object.entries({ adaos_device_id: 'image-generation-review', adaos_lang: 'ru',
      adaos_webspace_id: 'desktop-dev', adaos_hub_base: hub, adaos_local_hub_base: hub,
      adaos_hub_token: token, adaos_try_local_hub: '1', adaos_local_subnet_id: 'sn_6acf0c01',
      adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
  }, { hub, token })
  const page = await context.newPage()
  await page.goto('http://127.0.0.1:8100/?intent=webspace.open&zone=lo&subnet_id=sn_6acf0c01&webspace_id=desktop-dev&space_kind=development&expected_scenario_id=builder&try_local_hub=1',
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForFunction(() => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materializationReady, null, { timeout: 60000 })
  if (!await page.locator('.pending-action-item').count()) await page.locator('.pending-actions-fab').click()
  await page.waitForFunction(id => [...document.querySelectorAll('.pending-action-item')]
    .some(el => window.ng?.getContext(el)?.$implicit?.id === id), actionId, { timeout: 30000 })
  const index = await page.locator('.pending-action-item').evaluateAll((items, id) =>
    items.findIndex(el => window.ng?.getContext(el)?.$implicit?.id === id), actionId)
  assert.ok(index >= 0)
  const item = page.locator('.pending-action-item').nth(index)
  assert.match(await item.innerText(), /builder_sdk_control_skill:generate_icon/)
  await page.screenshot({ path: path.join(output, 'exact-image-approval.png'), fullPage: true })
  await item.getByRole('button', { name: /^(подтвердить|approve)(:|$)/i }).click()
  await page.waitForFunction(id => ![...document.querySelectorAll('.pending-action-item')]
    .some(el => window.ng?.getContext(el)?.$implicit?.id === id), actionId, { timeout: 30000 })
  await fs.writeFile(path.join(output, 'approval.json'), JSON.stringify({ actionId,
    scope: 'User-delegated owned TEST icon generation only', approvedThrough: 'Pending Actions UI' }, null, 2) + '\n', 'utf8')
} finally {
  await browser.close()
}
