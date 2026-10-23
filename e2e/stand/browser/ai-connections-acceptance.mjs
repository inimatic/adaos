// Non-paid live UI check. Credentials enter only browser memory, never artifacts.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const output = process.env.ADAOS_E2E_OUTPUT
const chatHost = process.env.ADAOS_E2E_CHAT_HOST
if (!output || !process.env.ADAOS_E2E_HUB_TOKEN) throw new Error('Explicit output and local owner credential required')
const browser = await chromium.launch({ headless: true })
const report = { ok: false, providerCalls: 0, errors: [], navigation: chatHost ? 'chat-account-button' : 'public-page-action' }
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(({ token, subnet, hub }) => {
    for (const [key, value] of Object.entries({ adaos_local_hub_url: hub, adaos_local_hub_token: token,
      adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
  }, { token: process.env.ADAOS_E2E_HUB_TOKEN, subnet: process.env.ADAOS_E2E_SUBNET_ID,
    hub: process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777' })
  const page = await context.newPage()
  page.on('pageerror', error => report.errors.push(String(error)))
  // Release notices are outside this connection-flow cohort. Use their public
  // Close handler even when another modal is above the notice's backdrop.
  const dismissReleaseNotice = async () => {
    if (await page.locator('.component-updates-backdrop').isVisible()) {
      await page.locator('.component-updates-panel__tools button').last().dispatchEvent('click')
    }
  }
  await page.addLocatorHandler(page.locator('.component-updates-backdrop'), dismissReleaseNotice)
  const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100')
  for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo',
    subnet_id: process.env.ADAOS_E2E_SUBNET_ID, webspace_id: chatHost || process.env.ADAOS_E2E_HOST || 'desktop',
    expected_scenario_id: chatHost ? 'context_chat' : 'builder', try_local_hub: '1' })) url.searchParams.set(key, value)
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  if (chatHost) {
    const account = page.getByTestId('account-button').filter({ visible: true })
    await expect(account).toBeVisible({ timeout: 60_000 })
    await dismissReleaseNotice()
    await account.click()
    await expect(page.getByTestId('manage-ai-connection')).toBeVisible({ timeout: 30_000 })
    report.chatConnection = await page.evaluate(() => {
      const widget = window.ng?.getComponent(document.querySelector('ada-project-list-widget'))
      return { sdkManagement: Boolean(widget?.s?.account?.management_application),
        navigationMethodPresent: typeof widget?.manageConnection === 'function' }
    })
    await expect(page.getByTestId('connect-account')).toHaveCount(0)
    await page.getByTestId('manage-ai-connection').click()
    report.navigated = true
  } else {
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
  }
  const actions = page.locator('[data-webui-widget-id="subscription-chatgpt-actions"]').filter({ visible: true })
  await expect(actions).toBeVisible({ timeout: 30_000 })
  await expect(actions.getByRole('button', { name: 'Continue with ChatGPT', exact: false })).toBeVisible()
  await expect(actions.getByRole('button', { name: 'Verify access', exact: false })).toBeVisible()
  await expect(actions.getByRole('button', { name: 'Disconnect ChatGPT', exact: false })).toBeVisible()
  await actions.getByRole('button', { name: 'Refresh ChatGPT', exact: true }).click()
  await dismissReleaseNotice()
  await fs.mkdir(output, { recursive: true })
  // Do not capture the Chat/history surface behind the management modal.
  await actions.screenshot({ path: path.join(output, chatHost ? 'chat-subscription-actions.png' : 'subscription-chatgpt.png') })
  report.ok = report.errors.length === 0
} catch (error) {
  report.errors.push(String(error))
} finally {
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, chatHost ? 'chat-subscription-ui-result.json' : 'subscription-ui-result.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
if (!report.ok) process.exitCode = 1
