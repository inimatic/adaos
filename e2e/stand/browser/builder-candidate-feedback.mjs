import { chromium } from '@playwright/test'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'

if (process.env.ENV_TYPE !== 'dev') {
  throw new Error('Builder candidate browser feedback requires ENV_TYPE=dev')
}

const scenario = String(process.env.ADAOS_E2E_SCENARIO_ID || '').trim()
const webspace = String(process.env.ADAOS_E2E_WEBSPACE_ID || '').trim()
const subnet = String(process.env.ADAOS_E2E_SUBNET_ID || '').trim()
const token = String(process.env.ADAOS_E2E_HUB_TOKEN || '').trim()
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'e2e/artifacts/builder-browser-feedback')
const hub = String(process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777').replace(/\/$/, '')
const client = String(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
const sourceDigest = String(process.env.ADAOS_E2E_SOURCE_DIGEST || '').trim()
const timeoutMs = Number(process.env.ADAOS_E2E_TIMEOUT_MS || 90_000)

if (!scenario || !webspace || !subnet || !token) {
  throw new Error('Scenario, paired DEV webspace, subnet, and local control token are required')
}

await fs.mkdir(output, { recursive: true })
const url = new URL(client)
for (const [key, value] of Object.entries({
  intent: 'webspace.open',
  zone: 'lo',
  subnet_id: subnet,
  webspace_id: webspace,
  space_kind: 'development',
  expected_scenario_id: scenario,
  try_local_hub: '1',
})) url.searchParams.set(key, value)

const report = {
  schema: 'adaos.builder.browser_feedback.v1',
  scenario_id: scenario,
  webspace_id: webspace,
  source_digest: sourceDigest || null,
  runtime_url: hub,
  client_url: client,
  samples: [],
  passed: false,
}
const browser = await chromium.launch({ headless: true })

const blockingTextPatterns = [
  /renderer failed to load/i,
  /scenario not found/i,
  /installed skill runtime is out of sync/i,
  /showing the last successful data while the source reconnects/i,
]

try {
  const layouts = {
    wide: { width: 1440, height: 1000 },
    compact: { width: 390, height: 844 },
  }
  for (const [layout, viewport] of Object.entries(layouts)) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({
        adaos_device_id: 'builder-browser-feedback',
        adaos_webspace_id: webspace,
        adaos_hub_base: hub,
        adaos_local_hub_base: hub,
        adaos_try_local_hub: '1',
        adaos_hub_token: token,
        adaos_local_subnet_id: subnet,
        adaos_selected_zone: 'lo',
        adaos_last_used_zone: 'lo',
        adaos_env_type: 'dev',
      })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace })

    const page = await context.newPage()
    page.setDefaultTimeout(timeoutMs)
    page.setDefaultNavigationTimeout(timeoutMs)
    const sample = {
      layout,
      viewport,
      checks: [],
      page_errors: [],
      console_errors: [],
      request_failures: [],
      hard_failures: [],
      warnings: [],
    }
    report.samples.push(sample)
    page.on('pageerror', error => sample.page_errors.push(String(error.message || error)))
    page.on('console', message => {
      if (message.type() === 'error') sample.console_errors.push(message.text())
    })
    page.on('response', response => {
      const request = response.request()
      if (response.status() < 400 || !['document', 'fetch', 'xhr'].includes(request.resourceType())) return
      sample.request_failures.push({
        method: request.method(),
        status: response.status(),
        url: response.url(),
      })
    })

    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync.materialization?.currentScenario === expected
      }, scenario, { timeout: timeoutMs })
      await page.waitForFunction(() => {
        return [...document.querySelectorAll('[data-webui-widget-id]')]
          .some(element => element.getClientRects().length > 0)
      }, undefined, { timeout: timeoutMs })
      await page.screenshot({ path: path.join(output, `${layout}-initial.png`), fullPage: true })

      if (layout === 'compact') {
        const compactTriggers = page.locator('.layout-region-trigger')
        const compactTriggerCount = await compactTriggers.count()
        if (compactTriggerCount) {
          const trigger = compactTriggers.first()
          if ((await trigger.getAttribute('aria-expanded')) !== 'true') {
            await trigger.click()
            await page.waitForTimeout(250)
          }
          const openRegions = page.locator('ada-layout-region.is-open')
          if ((await trigger.getAttribute('aria-expanded')) !== 'true' || !(await openRegions.count())) {
            sample.hard_failures.push('Compact detail/inspector disclosure did not open its semantic region')
          } else {
            sample.checks.push({ kind: 'compact-region-disclosure', count: compactTriggerCount })
            await page.screenshot({ path: path.join(output, `${layout}-disclosure.png`), fullPage: true })
            const close = openRegions.first().locator('.layout-region__header button').first()
            if (await close.count()) {
              await close.click()
              await page.waitForTimeout(150)
              if ((await trigger.getAttribute('aria-expanded')) !== 'false') {
                sample.hard_failures.push('Compact detail/inspector disclosure did not close')
              }
            }
          }
        }
      }

      const semanticTabs = page.locator('[role="tablist"] [role="tab"]')
      const tabCount = await semanticTabs.count()
      for (let index = 0; index < Math.min(tabCount, 12); index += 1) {
        const tab = semanticTabs.nth(index)
        if (!(await tab.isVisible())) continue
        await tab.focus()
        await tab.press('Enter')
        await page.waitForTimeout(350)
        const selected = await tab.getAttribute('aria-selected')
        if (selected !== 'true') {
          sample.hard_failures.push(`Semantic tab ${index + 1} did not become selected`)
        }
        if (index < 6) {
          await page.screenshot({ path: path.join(output, `${layout}-tab-${index + 1}.png`), fullPage: true })
        }
      }
      if (tabCount) sample.checks.push({ kind: 'semantic-tabs', count: tabCount })

      const diagnostics = await page.evaluate(patternSources => {
        const visible = element => {
          const style = getComputedStyle(element)
          return element.getClientRects().length > 0 && style.visibility !== 'hidden' && style.display !== 'none'
        }
        const widgetIds = [...document.querySelectorAll('[data-webui-widget-id]')]
          .filter(visible)
          .map(element => element.getAttribute('data-webui-widget-id'))
          .filter(Boolean)
        const unlabeled = [...document.querySelectorAll('button, a[href], input, select, textarea, [role="button"]')]
          .filter(visible)
          .filter(element => {
            const text = String(element.innerText || element.value || '').trim()
            return !text && !element.getAttribute('aria-label') && !element.getAttribute('title')
          })
          .slice(0, 24)
          .map(element => ({
            tag: element.tagName.toLowerCase(),
            role: element.getAttribute('role'),
            widget: element.closest('[data-webui-widget-id]')?.getAttribute('data-webui-widget-id') || null,
          }))
        const rendererFailures = [...document.querySelectorAll(
          '[data-webui-render-state="error"], [data-webui-render-state="unsupported"]',
        )].map(element => ({
          id: element.getAttribute('data-webui-widget-id'),
          type: element.getAttribute('data-webui-widget-type'),
          state: element.getAttribute('data-webui-render-state'),
          text: String(element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 500),
        }))
        const visibleRegions = [...document.querySelectorAll('ada-layout-region[data-region-role]')]
          .filter(visible)
          .map(element => {
            const box = element.getBoundingClientRect()
            return {
              id: element.getAttribute('data-region-id'),
              role: element.getAttribute('data-region-role'),
              top: box.top,
              bottom: box.bottom,
            }
          })
        const toolbar = visibleRegions.find(region => region.role === 'toolbar')
        const bodyRegions = visibleRegions.filter(region => ['collection', 'main', 'detail', 'inspector'].includes(region.role))
        const toolbarAfterBody = toolbar && bodyRegions.some(region => toolbar.top > region.top + 2)
        const bodyText = String(document.body?.innerText || '')
        const blockingText = patternSources
          .map(source => new RegExp(source, 'i'))
          .filter(pattern => pattern.test(bodyText))
          .map(pattern => pattern.source)
        return {
          current_scenario: window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario || null,
          viewport_width: innerWidth,
          document_width: document.documentElement.scrollWidth,
          viewport_height: innerHeight,
          document_height: document.documentElement.scrollHeight,
          visible_widget_ids: [...new Set(widgetIds)].slice(0, 160),
          visible_widget_count: widgetIds.length,
          unlabeled_interactives: unlabeled,
          renderer_failures: rendererFailures,
          visible_regions: visibleRegions,
          toolbar_after_body: Boolean(toolbarAfterBody),
          blocking_text: blockingText,
        }
      }, blockingTextPatterns.map(pattern => pattern.source))
      sample.diagnostics = diagnostics
      if (diagnostics.current_scenario !== scenario) {
        sample.hard_failures.push(`Expected scenario ${scenario}, got ${diagnostics.current_scenario || 'none'}`)
      }
      if (!diagnostics.visible_widget_count) sample.hard_failures.push('No visible WebUI widgets')
      if (diagnostics.document_width > diagnostics.viewport_width + 2) {
        sample.hard_failures.push(
          `Page overflows horizontally: ${diagnostics.document_width}px > ${diagnostics.viewport_width}px`,
        )
      }
      for (const pattern of diagnostics.blocking_text) {
        sample.hard_failures.push(`Blocking runtime message is visible: ${pattern}`)
      }
      if (diagnostics.unlabeled_interactives.length) {
        sample.warnings.push(`${diagnostics.unlabeled_interactives.length} visible interactive controls have no accessible name`)
      }
      for (const failure of diagnostics.renderer_failures) {
        sample.hard_failures.push(
          `Widget ${failure.id || failure.type || 'unknown'} is ${failure.state}: ${failure.text || 'no diagnostics'}`,
        )
      }
      if (diagnostics.toolbar_after_body) {
        sample.hard_failures.push('Semantic toolbar is rendered after the primary content region')
      }
      await page.screenshot({ path: path.join(output, `${layout}.png`), fullPage: true })
    } catch (error) {
      sample.hard_failures.push(String(error?.message || error))
      try {
        await page.screenshot({ path: path.join(output, `${layout}-failure.png`), fullPage: true })
      } catch {}
    } finally {
      await context.close()
    }
  }
} finally {
  await browser.close()
}

for (const sample of report.samples) {
  for (const error of sample.page_errors) sample.hard_failures.push(`Page error: ${error}`)
  for (const error of sample.console_errors) {
    if (/^Failed to load resource: the server responded with a status of (401|404)/i.test(error)) {
      sample.warnings.push(`Browser bootstrap resource warning: ${error}`)
    } else {
      sample.hard_failures.push(`Console error: ${error}`)
    }
  }
  for (const failure of sample.request_failures) {
    const target = new URL(failure.url)
    const expectedDevBootstrapMiss = failure.status === 404 && target.pathname === '/runtime-config.json'
    const expectedAuthProbe = failure.status === 401
      && target.pathname === '/api/node/status'
      && target.searchParams.get('profile') === 'probe'
    if (expectedDevBootstrapMiss || expectedAuthProbe) {
      sample.warnings.push(`Expected local bootstrap probe: HTTP ${failure.status} ${failure.method} ${failure.url}`)
    } else {
      sample.hard_failures.push(`HTTP ${failure.status} ${failure.method} ${failure.url}`)
    }
  }
}
report.passed = report.samples.length === 2 && report.samples.every(sample => sample.hard_failures.length === 0)
const screenshots = (await fs.readdir(output))
  .filter(name => name.endsWith('.png'))
  .sort()
for (const name of screenshots) {
  const bytes = await fs.readFile(path.join(output, name))
  report[`${name.replace(/\.png$/, '').replace(/-/g, '_')}_sha256`] = `sha256:${crypto.createHash('sha256').update(bytes).digest('hex')}`
}
await fs.writeFile(path.join(output, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8')
console.log(JSON.stringify(report, null, 2))
if (!report.passed) process.exitCode = 1
