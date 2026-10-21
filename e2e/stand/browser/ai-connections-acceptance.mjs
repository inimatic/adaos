// Non-paid live UI check. Credentials enter only browser memory, never artifacts.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal, dismissSystemOverlays } from './prototype-navigation.mjs'

const output = process.env.ADAOS_E2E_OUTPUT
if (!output || !process.env.ADAOS_E2E_HUB_TOKEN) throw new Error('Explicit output and local owner credential required')
const browser = await chromium.launch({ headless: true })
const report = { ok: false, providerCalls: 0, errors: [], navigation: 'public-page-action' }
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ token, subnet, hub }) => {
    for (const [key, value] of Object.entries({ adaos_local_hub_url: hub, adaos_local_hub_token: token,
      adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
  }, { token: process.env.ADAOS_E2E_HUB_TOKEN, subnet: process.env.ADAOS_E2E_SUBNET_ID,
    hub: process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777' })
  const page = await context.newPage()
  page.on('pageerror', error => report.errors.push(String(error)))
  await installSystemOverlayDismissal(page)
  const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100')
  for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo',
    subnet_id: process.env.ADAOS_E2E_SUBNET_ID, webspace_id: process.env.ADAOS_E2E_HOST || 'desktop',
    expected_scenario_id: 'builder', try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  await page.waitForFunction(() => !!document.querySelector('ada-chat-widget'), { timeout: 60_000 })
  // Exercise the same public navigation contract used by Chat's management
  // button, without clicking another project's conversation or sending a turn.
  const navigated = await page.evaluate(async () => {
    const component = window.ng?.getComponent(document.querySelector('ada-chat-widget'))
    if (!component?.actions?.handle) throw new Error('Public action handler not available in local development UI')
    return component.actions.handle({ on: 'click', type: 'navigate', params: {
      to: 'subscription_status_skill.subscription_status_modal', surface: 'modal', modalId: 'subscription_status_modal' } })
  })
  report.navigated = navigated
  const actions = page.locator('[data-webui-widget-id="subscription-chatgpt-actions"]').filter({ visible: true })
  await expect(actions).toBeVisible({ timeout: 30_000 })
  await expect(actions.getByRole('button', { name: 'Continue with ChatGPT', exact: false })).toBeVisible()
  await expect(actions.getByRole('button', { name: 'Verify access', exact: false })).toBeVisible()
  await expect(actions.getByRole('button', { name: 'Disconnect ChatGPT', exact: false })).toBeVisible()
  await actions.getByRole('button', { name: 'Refresh ChatGPT', exact: true }).click()
  await dismissSystemOverlays(page)
  await fs.mkdir(output, { recursive: true })
  await page.screenshot({ path: path.join(output, 'subscription-chatgpt.png') })
  report.ok = report.errors.length === 0
} catch (error) {
  report.errors.push(String(error))
} finally {
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, 'subscription-ui-result.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.ok) process.exitCode = 1
