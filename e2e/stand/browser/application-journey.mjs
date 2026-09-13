// Independent UI acceptance plans are not part of model authoring context.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const env = process.env
const plan = JSON.parse(await fs.readFile(env.ADAOS_E2E_JOURNEY_PLAN, 'utf8'))
const pin = JSON.parse(await fs.readFile(env.ADAOS_E2E_SNAPSHOT_PIN, 'utf8'))
const checkpoint = JSON.parse(await fs.readFile(env.ADAOS_E2E_CHECKPOINT, 'utf8'))
const scenario = env.ADAOS_E2E_SCENARIO_ID
const webspace = env.ADAOS_E2E_WEBSPACE_ID
const token = env.ADAOS_E2E_HUB_TOKEN
const hub = env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8778'
const subnet = env.ADAOS_E2E_SUBNET_ID
const locale = env.ADAOS_E2E_LOCALE || 'en'
if (env.ENV_TYPE !== 'dev' || pin.stage !== 'automation' || pin.scenario_id !== scenario
  || !pin.revision.startsWith('task.') || pin.preview_webspace_id !== webspace || !token
  || !checkpoint.cleanup?.test || !checkpoint.context?.retain_test_projects
  || !checkpoint.context.owned_artifacts.some(item => item.primary_ref === `scenario:${scenario}`)) {
  throw new Error('Requires an explicit owned TEST Automation pin and existing DEV preview')
}
if (plan.schema !== 'adaos.e2e.application_journey.v1' || !plan.steps?.length) throw new Error('Journey plan is empty')
const output = path.resolve(env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output, { recursive: true })
const url = new URL(env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: webspace, space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) {
  url.searchParams.set(key, value)
}
const report = { scope: 'Independent DEV-owner browser journey, not delegated authorization',
  scenario, task: pin.revision, webspace, plan, samples: [] }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const sample = { layout, checks: [], errors: [], network: [], marker: `E2E-${layout}-${Date.now()}` }
    report.samples.push(sample)
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace, locale }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-application-journey', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: locale })) {
        localStorage.setItem(key, value)
      }
    }, { hub, token, subnet, webspace, locale })
    const page = await context.newPage()
    page.setDefaultTimeout(15_000)
    page.on('pageerror', error => sample.errors.push(error.message))
    const responses = []
    const started = new WeakMap()
    page.on('request', request => started.set(request, Date.now()))
    page.on('response', response => {
      if (new URL(response.url()).pathname !== '/api/tools/call') return
      responses.push(response.json().then(body => sample.network.push({
        status: response.status(), elapsedMs: Date.now() - started.get(response.request()),
        request: response.request().postDataJSON(), body,
      })).catch(() => {}))
    })
    const expand = value => typeof value === 'string' ? value.replaceAll('${marker}', sample.marker) : value
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
    const locator = step => step.role ? (step.widget ? host(step.widget) : page).getByRole(step.role, { name: step.namePattern ? new RegExp(step.namePattern, 'i') : step.name, exact: true }) : step.selector ? (step.hasText ? page.locator(step.selector).filter({ hasText: new RegExp(step.hasText, 'i') }) : page.locator(step.selector)) : step.field
      ? host(step.widget).locator(`[data-webui-field-id=${JSON.stringify(step.field)}]`).locator(step.control || 'input,textarea,select')
      : step.widget ? host(step.widget) : page.locator('body')
    const command = step => (step.widget ? host(step.widget) : page).locator(`[data-command-id=${JSON.stringify(step.command)}]`)
    const ready = async () => {
      await page.waitForFunction(id => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.providerSynced && sync?.materializationReady && sync.materialization.currentScenario === id
      }, scenario, { timeout: 60_000 })
      await page.evaluate(() => document.fonts.ready)
    }
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await ready()
      for (const step of plan.steps) {
        sample.active = step.id
        const start = Date.now()
        switch (step.type) {
          case 'command': await command(step).click(); break
          case 'click': await locator(step).click(); break
          case 'row': {
            const row = host(step.widget).locator(step.rows || 'tr.row-selectable,.collection-focus-item').filter({ hasText: expand(step.text) })
            await expect(row).toHaveCount(1)
            await row.click()
            break
          }
          case 'fill': await locator(step).fill(expand(step.value)); break
          case 'select': {
            const field = host(step.widget).locator(`[data-webui-field-id=${JSON.stringify(step.field)}]`)
            if (await field.locator('select').count()) {
              await field.locator('select').selectOption(step.label ? { label: expand(step.label) } : { value: expand(step.value) })
            } else {
              await field.getByRole('radio', { name: expand(step.label || step.value), exact: true }).check()
            }
            break
          }
          case 'check': await locator(step).setChecked(step.checked); break
          case 'text': await expect(locator(step)).toContainText(expand(step.text)); break
          case 'notText': await expect(locator(step)).not.toContainText(expand(step.text)); break
          case 'value': await expect(locator(step)).toHaveValue(expand(step.value)); break
          case 'count': await expect(locator(step)).toHaveCount(step.count); break
          case 'disabled': await expect(command(step)).toBeDisabled(); break
          case 'enabled': await expect(command(step)).toBeEnabled(); break
          case 'dismiss': {
            const modal = page.locator('ion-modal').last()
            await modal.getByRole('button', { name: /^(Close|Закрыть)$/ }).click()
            await expect(modal).not.toBeVisible()
            break
          }
          case 'submit': {
            const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/tools/call'
              && response.request().postDataJSON()?.tool?.endsWith(`:${step.tool}`))
            void pending.catch(() => {})
            await command(step).click()
            if (step.confirm) await page.locator('ion-alert').last().getByRole('button', { name: step.confirm, exact: true }).click()
            const response = await pending
            const body = await response.json()
            const result = body.result ?? body
            expect(response.ok() && body.ok !== false && result.ok !== false).toBe(step.ok !== false)
            if (step.error) expect(result.error).toBe(step.error)
            if (step.ok === false || step.keepOpen) await expect(host(step.widget)).toBeVisible()
            else await expect(host(step.widget)).toHaveCount(0)
            break
          }
          case 'drag': await locator(step).dragTo(page.locator(step.target)); break
          case 'reload': await page.reload({ waitUntil: 'domcontentloaded' }); await ready(); break
          case 'screenshot': await page.screenshot({ path: path.join(output, `${layout}-${step.id}.png`), fullPage: true }); break
          default: throw new Error(`Unknown journey action: ${step.type}`)
        }
        sample.checks.push({ id: step.id, status: 'passed', durationMs: Date.now() - start })
        sample.active = null
        console.log(`${layout}: ${step.id} passed`)
      }
    } catch (error) {
      sample.failure = error.message
      sample.modalControls = await page.locator('ion-modal ion-button,ion-modal button').evaluateAll(nodes => nodes.map(node => node.outerHTML))
    }
    await Promise.all(responses)
    sample.text = await page.locator('body').innerText()
    await page.screenshot({ path: path.join(output, `${layout}-final.png`), fullPage: true })
    await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
    await context.close()
  }
} finally { await browser.close() }
report.passed = report.samples.length === 2 && report.samples.every(sample => !sample.failure && !sample.errors.length)
await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify({ passed: report.passed, samples: report.samples.map(s => ({ layout: s.layout, checks: s.checks.length, failure: s.failure })) }))
if (!report.passed) process.exitCode = 1
