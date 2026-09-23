import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'

const scenario = process.env.ADAOS_E2E_SCENARIO_ID || 'gmail_mail_client'
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID || 'desktop-dev'
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const token = process.env.ADAOS_E2E_HUB_TOKEN
const expectedRevision = process.env.ADAOS_E2E_REVISION || '006'
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'e2e/artifacts/gmail-mail-client-interactions')
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const client = process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/'

if (process.env.ENV_TYPE !== 'dev') throw new Error('Gmail Prototype proof requires ENV_TYPE=dev')
if (!subnet || !token) throw new Error('Local subnet and control token are required')

const assert = (condition, message) => {
  if (!condition) throw new Error(message)
}
const waitForCount = async (locator, count) => {
  await locator.page().waitForFunction(
    ([selector, expected]) => document.querySelectorAll(selector).length === expected,
    [await locator.evaluateAll(elements => {
      const element = elements[0]
      if (!element) return ''
      const marker = `e2e-${crypto.randomUUID()}`
      element.closest('[data-webui-widget-id]')?.setAttribute('data-e2e-count-root', marker)
      return `[data-e2e-count-root="${marker}"] .collection-focus-item`
    }), count],
  )
}
const stateSnapshot = page => page.locator('ada-query-toolbar-widget').evaluate(element => {
  const component = window.ng?.getComponent(element)
  return component?.state?.getSnapshot?.() || null
})

const target = new URL(client)
for (const [key, value] of Object.entries({
  intent: 'webspace.open', zone: 'lo', subnet_id: subnet, webspace_id: webspace,
  space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1',
})) target.searchParams.set(key, value)

await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const samples = []
try {
  for (const [layout, viewport] of Object.entries({
    wide: { width: 1440, height: 1000 },
    compact: { width: 390, height: 844 },
  })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({
        adaos_device_id: `e2e-gmail-${crypto.randomUUID()}`,
        adaos_webspace_id: webspace,
        adaos_hub_base: hub,
        adaos_local_hub_base: hub,
        adaos_try_local_hub: '1',
        adaos_hub_token: token,
        adaos_local_subnet_id: subnet,
        adaos_selected_zone: 'lo',
        adaos_last_used_zone: 'lo',
        adaos_lang: 'en',
      })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace })

    const page = await context.newPage()
    page.setDefaultTimeout(30_000)
    page.setDefaultNavigationTimeout(60_000)
    const errors = []
    const failedResponses = []
    const externalRequests = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('request', request => {
      const url = new URL(request.url())
      if (/googleapis\.com$|accounts\.google\.com$/i.test(url.hostname)) {
        externalRequests.push({ method: request.method(), url: request.url() })
      }
    })
    page.on('response', response => {
      const url = new URL(response.url())
      if (url.pathname.startsWith('/api/') && response.status() >= 400) {
        failedResponses.push({ path: url.pathname, status: response.status() })
      }
    })

    const checks = []
    let failure = null
    try {
      await page.goto(target.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(([expectedScenario, revision]) => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady
          && sync?.materialization?.currentScenario === expectedScenario
          && sync?.materialization?.materializationRevision === revision
      }, [scenario, expectedRevision], { timeout: 60_000 })
      await page.locator('[data-webui-widget-id="message-list"] .collection-focus-item').first().waitFor()

      const messageRows = page.locator('[data-webui-widget-id="message-list"] .collection-focus-item')
      assert(await messageRows.count() === 3, 'Expected three Prototype messages')
      checks.push({ id: 'messages.initial', status: 'passed', count: 3 })

      const search = page.locator('[data-query-id="q-search"] input')
      await search.fill('receipt')
      await waitForCount(messageRows, 1)
      assert((await messageRows.first().innerText()).includes('Your receipt'), 'Search selected the wrong message')
      checks.push({ id: 'messages.search', status: 'passed', query: 'receipt', count: 1 })

      await page.getByRole('button', { name: 'Reset filters', exact: true }).click()
      await waitForCount(messageRows, 3)
      await page.getByRole('button', { name: /^Filters/ }).click()
      await page.locator('[data-query-id="f-unread"] select').selectOption({ label: 'Unread' })
      await waitForCount(messageRows, 2)
      assert((await messageRows.allInnerTexts()).every(text => !text.includes('Your receipt')), 'Unread filter retained read mail')
      checks.push({ id: 'messages.unread-filter', status: 'passed', count: 2 })

      await page.getByRole('button', { name: 'Reset filters', exact: true }).click()
      await waitForCount(messageRows, 3)
      await messageRows.filter({ hasText: 'Your receipt' }).click()
      await page.locator('[data-webui-widget-id="message-details"]').getByText('Store <noreply@store.test>', { exact: true }).waitFor()
      const selectedState = await stateSnapshot(page)
      assert(selectedState?.selectedMessageId === 'm2', 'Selection did not update page state')
      checks.push({ id: 'messages.select-details', status: 'passed', selectedMessageId: 'm2' })

      const receiptRow = messageRows.filter({ hasText: 'Your receipt' })
      await receiptRow.locator('[data-command-id="row-star"]').click()
      const rowActionState = await stateSnapshot(page)
      assert(rowActionState?.pendingAction === 'star_row' && rowActionState?.rowId === 'm2', 'Row action lost its item identity')
      checks.push({ id: 'messages.row-action', status: 'passed', action: 'star_row', rowId: 'm2' })

      await page.locator('[data-webui-widget-id="mail-topbar-actions"] [data-command-id="action-compose"]').click()
      const modal = page.locator('ion-modal:not(.overlay-hidden) ada-schema-modal')
      await modal.waitFor()
      await modal.getByText('Body (no message bodies are persisted in prototype)', { exact: true }).waitFor()
      await modal.locator('[data-command-id="compose-cancel"]').click()
      await modal.waitFor({ state: 'detached' })
      checks.push({ id: 'compose.open-cancel', status: 'passed' })

      await page.locator('[data-webui-widget-id="connection-banner"] [data-command-id="btn-connect"]').click()
      await page.waitForFunction(() => {
        const element = document.querySelector('ada-query-toolbar-widget')
        return window.ng?.getComponent(element)?.state?.get?.('connectionStatus') === 'connecting'
      })
      assert(await page.locator('[data-command-id="btn-connect"]:visible').count() === 0, 'Connect action remained visible after transition')
      checks.push({ id: 'connection.prototype-transition', status: 'passed', state: 'connecting' })

      const state = await stateSnapshot(page)
      const text = await page.locator('body').innerText()
      const serialized = JSON.stringify({ state, text })
      assert(!/(ya29\.|Bearer\s+[A-Za-z0-9._-]{12,}|refresh_token|access_token)/i.test(serialized), 'Sensitive credential-shaped data reached the rendered Prototype')
      assert(externalRequests.length === 0, 'Prototype contacted Gmail before Automation acceptance')
      assert(errors.length === 0, `Page errors: ${errors.join('; ')}`)
      assert(failedResponses.length === 0, `Failed API responses: ${JSON.stringify(failedResponses)}`)
      const geometry = await page.evaluate(() => ({
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        rowHeights: [...document.querySelectorAll('[data-webui-widget-id="message-list"] .collection-focus-item')]
          .map(element => Math.round(element.getBoundingClientRect().height)),
      }))
      assert(geometry.documentWidth <= geometry.viewportWidth, 'Document has horizontal overflow')
      assert(Math.max(...geometry.rowHeights) < 150, 'Message rows expanded beyond the compact interaction budget')
      checks.push({ id: 'security.prototype-boundary', status: 'passed', externalRequests: 0 })
      checks.push({ id: 'layout.geometry', status: 'passed', ...geometry })
      await page.screenshot({ path: path.join(output, `${layout}.png`), fullPage: true })
    } catch (error) {
      failure = String(error?.stack || error)
    }
    samples.push({ layout, viewport, checks, errors, failedResponses, externalRequests, failure })
    await context.close()
  }
} finally {
  await browser.close()
}

const report = {
  schema: 'adaos.e2e.gmail-mail-client-prototype.v1',
  scenario,
  webspace,
  revision: expectedRevision,
  passed: samples.every(sample => !sample.failure && !sample.errors.length && !sample.failedResponses.length),
  samples,
}
await fs.writeFile(path.join(output, 'interactions.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8')
console.log(JSON.stringify({ passed: report.passed, output, samples: samples.map(sample => ({ layout: sample.layout, failure: sample.failure })) }))
if (!report.passed) process.exitCode = 1
