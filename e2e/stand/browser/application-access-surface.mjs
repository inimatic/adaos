import { chromium, expect } from '@playwright/test'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'

if (process.env.ENV_TYPE !== 'dev') throw new Error('Application access review requires ENV_TYPE=dev')
const scenario = process.env.ADAOS_E2E_SCENARIO_ID || ''
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID || ''
const subnet = process.env.ADAOS_E2E_SUBNET_ID || ''
const token = process.env.ADAOS_E2E_HUB_TOKEN || ''
const expectedWebui = process.env.ADAOS_E2E_EXPECTED_WEBUI || ''
if (!scenario.startsWith('test_') || !webspace || !subnet || !token || !expectedWebui) {
  throw new Error('Explicit TEST scenario, webspace, subnet, token, and expected WebUI are required')
}

const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'artifacts/application-access')
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8778'
const client = process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/'
const source = await fs.readFile(expectedWebui)
const sourceDigest = `sha256:${crypto.createHash('sha256').update(source).digest('hex')}`
const expectedDocument = JSON.parse(source.toString('utf8'))
const expectedState = expectedDocument.ui.application.desktop.pageSchema.initialState
const applicationId = expectedState.selectedApplicationId
const releaseDigest = expectedState.selectedReleaseDigest
if (!applicationId || !releaseDigest) throw new Error('Expected WebUI must pin an Application and release')
await fs.mkdir(output, { recursive: true })

const url = new URL(client)
for (const [key, value] of Object.entries({
  intent: 'webspace.open', zone: 'lo', subnet_id: subnet, webspace_id: webspace,
  space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1',
})) url.searchParams.set(key, value)

const tabChecks = {
  permissions: ['application-access-permissions', 'application-permission-profiler', 'application-privacy-observation'],
  access: ['application-access-access', 'access-grant-form', 'access-simulation-form'],
  roles: ['application-access-roles'],
  connected_accounts: ['application-access-connected_accounts', 'connected-account-form'],
  release_readiness: ['application-access-release_readiness', 'application-final-verification-form'],
  activity: ['application-access-activity', 'application-access-reviews'],
  users_access: ['users-access-tabs', 'users-access-people'],
}
const tabContent = {
  permissions: 'sha256:reviewed-profile',
  access: 'user:masha',
  roles: 'Owner',
  connected_accounts: 'calendar',
  release_readiness: 'passed',
  activity: 'application.permission.allow',
  users_access: 'user:masha',
}
const userTabs = ['people', 'guests', 'children', 'devices', 'sessions', 'application_access', 'activity']
const report = { schema: 'adaos.e2e.application_access_browser.v1', scenario, webspace, sourceDigest, samples: [], passed: false }
const browser = await chromium.launch({ headless: true })

try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({
        adaos_device_id: 'e2e-application-access', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1',
        adaos_hub_token: token, adaos_local_subnet_id: subnet,
        adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en',
      })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace })
    const page = await context.newPage()
    page.setDefaultTimeout(30_000)
    page.setDefaultNavigationTimeout(60_000)
    const sample = { layout, viewport, checks: [], errors: [], requestFailures: [] }
    report.samples.push(sample)
    page.on('pageerror', error => sample.errors.push(error.message))
    page.on('response', response => {
      const target = new URL(response.url())
      if (['/api/tools/call', '/api/admin/root_mcp/call'].includes(target.pathname) && response.status() >= 400) {
        sample.requestFailures.push({ path: target.pathname, status: response.status() })
      }
    })

    try {
      const accessResponse = await context.request.get(
        `${hub}/api/application-access/applications/${encodeURIComponent(applicationId)}?release_digest=${encodeURIComponent(releaseDigest)}`,
        { headers: { 'X-AdaOS-Token': token } },
      )
      const accessBody = await accessResponse.json()
      if (!accessResponse.ok() || accessBody.schema !== 'adaos.application.access_surface.v1') {
        throw new Error(`Application access API failed: HTTP ${accessResponse.status()}`)
      }
      const sectionNames = Object.keys(accessBody.sections || {}).sort()
      if (sectionNames.join(',') !== ['access', 'activity', 'connected_accounts', 'permissions', 'release_readiness', 'roles'].join(',')) {
        throw new Error(`Application access API returned incomplete sections: ${sectionNames.join(', ')}`)
      }
      if (!accessBody.sections.permissions?.profile?.required?.length || !accessBody.sections.roles?.length) {
        throw new Error('Application access API returned an empty permission or role profile')
      }
      sample.checks.push({ kind: 'release-bound-access-api', applicationId, releaseDigest, sections: sectionNames })

      const rootMcpResponse = await context.request.post(`${hub}/api/admin/root_mcp/call`, {
        headers: { 'X-AdaOS-Token': token },
        data: {
          tool_id: 'applications.access.show',
          arguments: { application_id: applicationId, release_digest: releaseDigest },
          dry_run: true,
        },
      })
      const rootMcpBody = await rootMcpResponse.json()
      if (!rootMcpResponse.ok() || !rootMcpBody.ok || !rootMcpBody.response?.result?.access?.sections?.permissions?.profile) {
        throw new Error(`Application access Root MCP bridge failed: HTTP ${rootMcpResponse.status()}`)
      }
      sample.checks.push({ kind: 'root-mcp-application-access-bridge', tool: 'applications.access.show' })
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync.materialization?.currentScenario === expected
      }, scenario, { timeout: 60_000 })
      await page.locator('[data-webui-widget-id="catalog-applications"]').waitFor()
      const family = page.locator('[data-webui-widget-id="catalog-applications"]')
        .locator('tr.row-selectable, .collection-focus-item').filter({ hasText: 'Family Tasks' }).first()
      if (layout === 'wide' && await family.count()) {
        await expect(family).toBeVisible({ timeout: 30_000 })
        await family.click()
        sample.checks.push({ kind: 'application-selection', source: 'available-catalog' })
      } else {
        sample.checks.push({ kind: 'application-selection', source: 'release-bound-initial-state' })
      }
      await expect(page.locator('[data-webui-widget-id="tabs"]')).toBeVisible()

      for (const [tab, widgetIds] of Object.entries(tabChecks)) {
        const button = page.locator('[data-webui-widget-id="tabs"]')
          .locator(`[data-command-id=${JSON.stringify(tab)}]`)
        await expect(button).toBeVisible()
        await expect(button).toBeInViewport()
        await button.focus()
        await button.press('Enter')
        const selectedTabVisible = await button.evaluate(element => {
          const tablist = element.closest('[role="tablist"]')
          const buttonRect = element.getBoundingClientRect()
          const listRect = tablist?.getBoundingClientRect()
          return Boolean(listRect && buttonRect.left >= listRect.left && buttonRect.right <= listRect.right)
        })
        if (!selectedTabVisible) throw new Error(`Selected Application tab is clipped: ${tab}`)
        for (const widgetId of widgetIds) {
          await expect(page.locator(`[data-webui-widget-id=${JSON.stringify(widgetId)}]`)).toBeVisible()
        }
        await expect(page.locator('body')).toContainText(tabContent[tab])
        if (tab === 'access') {
          const grant = page.locator('[data-webui-widget-id="application-access-access"]')
            .locator('tr.row-selectable, .collection-focus-item').filter({ hasText: 'user:masha' }).first()
          await grant.click()
          await expect(page.locator('[data-webui-widget-id="access-change-form"]')).toBeVisible()
          await expect(page.locator('[data-webui-widget-id="access-revoke-actions"]')).toBeVisible()
        }
        sample.checks.push({ kind: 'application-tab-keyboard', tab, widgets: widgetIds })
        if (['permissions', 'access', 'release_readiness', 'users_access'].includes(tab)) {
          await page.screenshot({ path: path.join(output, `${layout}-${tab}.png`), fullPage: true })
        }
      }

      for (const tab of userTabs) {
        const button = page.locator('[data-webui-widget-id="users-access-tabs"]')
          .locator(`[data-command-id=${JSON.stringify(tab)}]`)
        await expect(button).toBeVisible()
        await expect(button).toBeInViewport()
        await button.focus()
        await button.press('Enter')
        const selectedUserTabVisible = await button.evaluate(element => {
          const tablist = element.closest('[role="tablist"]')
          const buttonRect = element.getBoundingClientRect()
          const listRect = tablist?.getBoundingClientRect()
          return Boolean(listRect && buttonRect.left >= listRect.left && buttonRect.right <= listRect.right)
        })
        if (!selectedUserTabVisible) throw new Error(`Selected Users & Access tab is clipped: ${tab}`)
        await expect(page.locator(`[data-webui-widget-id=${JSON.stringify(`users-access-${tab}`)}]`)).toBeVisible()
        await expect(page.locator(`[data-webui-widget-id=${JSON.stringify(`users-access-${tab}`)}]`)).not.toContainText(/^No /)
        sample.checks.push({ kind: 'users-access-tab-keyboard', tab })
      }

      const geometry = await page.evaluate(() => ({
        viewportWidth: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        currentScenario: window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario,
        visibleWidgetCount: [...document.querySelectorAll('[data-webui-widget-id]')]
          .filter(element => element.getClientRects().length > 0).length,
      }))
      sample.geometry = geometry
      if (geometry.documentWidth > geometry.viewportWidth) throw new Error('Application access surface overflows horizontally')
      await page.screenshot({ path: path.join(output, `${layout}.png`), fullPage: true })
    } catch (error) {
      sample.failure = error.message
    } finally {
      await context.close()
    }
  }
} finally {
  await browser.close()
}

report.passed = report.samples.every(sample => !sample.failure && !sample.errors.length && !sample.requestFailures.length)
await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify(report, null, 2))
if (!report.passed) process.exitCode = 1
