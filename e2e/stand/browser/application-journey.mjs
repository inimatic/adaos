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
const expectedRuntimeSource = env.ADAOS_E2E_EXPECTED_RUNTIME_SOURCE || null
if (expectedRuntimeSource && !['dev', 'trial', 'workspace'].includes(expectedRuntimeSource)) {
  throw new Error('ADAOS_E2E_EXPECTED_RUNTIME_SOURCE must be dev, trial, or workspace')
}
const spaceKind = expectedRuntimeSource === 'workspace' || expectedRuntimeSource === 'trial'
  ? 'workspace' : 'development'
if (env.ENV_TYPE !== 'dev' || pin.stage !== 'automation' || pin.scenario_id !== scenario
  || !pin.revision.startsWith('task.')
  || (spaceKind === 'development' && pin.preview_webspace_id !== webspace) || !token
  || !checkpoint.cleanup?.test || !checkpoint.context?.retain_test_projects
  || !checkpoint.context.owned_artifacts.some(item => item.primary_ref === `scenario:${scenario}`)) {
  throw new Error('Requires an explicit owned TEST Automation pin and admitted runtime target')
}
if (plan.schema !== 'adaos.e2e.application_journey.v1' || !plan.steps?.length) throw new Error('Journey plan is empty')
const stepIds = new Set()
for (const step of plan.steps) {
  if (!step.id || stepIds.has(step.id)) throw new Error('Journey steps require unique IDs')
  stepIds.add(step.id)
  if (step.layouts && (!Array.isArray(step.layouts) || !step.layouts.length
    || step.layouts.some(layout => !['wide', 'compact'].includes(layout)))) {
    throw new Error(`Invalid journey layouts: ${step.id}`)
  }
}
const output = path.resolve(env.ADAOS_E2E_OUTPUT)
await fs.mkdir(output, { recursive: true })
const url = new URL(env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: webspace, space_kind: spaceKind, expected_scenario_id: scenario, try_local_hub: '1' })) {
  url.searchParams.set(key, value)
}
const report = { scope: 'Independent owned TEST browser journey, not delegated authorization',
  scenario, task: pin.revision, webspace, spaceKind, expectedRuntimeSource, plan, samples: [] }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const sample = { layout, startedAt: Date.now(), checks: [], errors: [], requests: [], network: [], marker: `E2E-${layout}-${Date.now()}` }
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
    page.on('request', request => {
      started.set(request, Date.now())
      const pathname = new URL(request.url()).pathname
      if (pathname === '/api/tools/call') {
        sample.requests.push({ kind: 'tool', elapsedMs: Date.now() - sample.startedAt, body: request.postDataJSON() })
      } else if (pathname.includes('/attachments')) {
        sample.requests.push({ kind: 'attachment', method: request.method(), pathname,
          elapsedMs: Date.now() - sample.startedAt })
      }
    })
    page.on('response', response => {
      const pathname = new URL(response.url()).pathname
      if (pathname.includes('/attachments')) {
        sample.network.push({ kind: 'attachment', method: response.request().method(), pathname,
          status: response.status(), elapsedMs: Date.now() - started.get(response.request()) })
        return
      }
      if (pathname !== '/api/tools/call') return
      responses.push(response.json().then(body => sample.network.push({
        status: response.status(), elapsedMs: Date.now() - started.get(response.request()), timing: response.request().timing(),
        runtimeSource: response.headers()['x-adaos-runtime-source'] || null,
        releaseDigest: response.headers()['x-adaos-release-digest'] || null,
        request: response.request().postDataJSON(), body,
      })).catch(() => {}))
    })
    const variables = { marker: sample.marker }
    const expand = value => typeof value === 'string' ? value.replace(/\$\{([A-Za-z0-9_]+)\}/g, (_, key) => {
      if (!(key in variables)) throw new Error(`Unknown journey variable: ${key}`)
      return variables[key]
    }) : value
    const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
    const locator = step => step.role ? (step.widget ? host(step.widget) : page).getByRole(step.role, { name: step.namePattern ? new RegExp(expand(step.namePattern), 'i') : expand(step.name), exact: true }) : step.selector ? (step.hasText ? page.locator(expand(step.selector)).filter({ hasText: new RegExp(expand(step.hasText), 'i') }) : page.locator(expand(step.selector))) : step.field
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
        if (step.layouts && !step.layouts.includes(layout)) {
          sample.checks.push({ id: step.id, status: 'not_applicable', layout })
          continue
        }
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
          case 'upload': {
            const field = host(step.widget).locator(`[data-webui-field-id=${JSON.stringify(step.field)}]`)
            const input = field.locator('input[type=file]')
            await expect(input).toHaveCount(1)
            const filePath = path.resolve(expand(step.file))
            const pending = page.waitForResponse(response => {
              const target = new URL(response.url())
              return response.request().method() === 'PUT'
                && target.pathname.includes('/attachments')
                && (!step.tool || target.pathname.includes(`/${step.tool}/attachments`))
            })
            void pending.catch(() => {})
            await input.setInputFiles(filePath)
            const response = await pending
            if (!response.ok()) throw new Error(`Attachment upload failed: HTTP ${response.status()}`)
            await expect(field).toContainText(path.basename(filePath))
            break
          }
          case 'select': {
            const field = step.selector ? locator(step) : host(step.widget).locator(`[data-webui-field-id=${JSON.stringify(step.field)}]`)
            await expect(field).toBeVisible({ timeout: 15_000 })
            if (step.selector || await field.locator('select').count()) {
              const select = step.selector ? field : field.locator('select')
              if (step.label) await select.selectOption({ label: expand(step.label) })
              else {
                // Angular ngValue prefixes the native option value with its internal index.
                const value = String(expand(step.value))
                const option = () => select.locator('option').evaluateAll((nodes, expected) =>
                  nodes.find(node => node.value === expected || node.value.replace(/^\d+: /, '') === expected)?.value, value)
                await expect.poll(option, { timeout: 15_000 }).toBeTruthy()
                await select.selectOption({ value: await option() })
              }
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
          case 'disabled':
          case 'enabled': {
            const control = step.command ? command(step) : locator(step)
            await expect(control).toBeVisible()
            if (await control.evaluate(node => node.localName === 'ion-button')) {
              if (step.type === 'disabled') await expect(control).toHaveAttribute('aria-disabled', 'true')
              else await expect(control).not.toHaveAttribute('aria-disabled', 'true')
            } else if (step.type === 'disabled') await expect(control).toBeDisabled()
            else await expect(control).toBeEnabled()
            break
          }
          case 'dismiss': {
            const modal = page.locator('ion-modal').last()
            await modal.getByRole('button', { name: /^(Close|Закрыть)$/i }).click()
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
            for (const [key, field] of Object.entries(step.capture || {})) {
              const value = field.split('.').reduce((item, segment) => item?.[segment], result)
              if (!['string', 'number', 'boolean'].includes(typeof value)) throw new Error(`Missing scalar capture: ${field}`)
              variables[key] = value
            }
            if (step.error) expect(result.error).toBe(step.error)
            if (step.ok === false || step.keepOpen) await expect(host(step.widget)).toBeVisible()
            else await expect(host(step.widget)).toHaveCount(0)
            break
          }
          case 'nextToolArguments': {
            if (!/^[A-Za-z0-9_]+$/.test(step.tool) || !step.arguments || Array.isArray(step.arguments)
              || typeof step.arguments !== 'object' || !Object.keys(step.arguments).length
              || Object.keys(step.arguments).some(key => key.startsWith('_'))) {
              throw new Error('Fault injection requires a local tool and explicit public argument overrides')
            }
            let consumed = false
            const intercept = async route => {
              try {
                const body = route.request().postDataJSON()
                if (consumed || !body?.tool?.endsWith(`:${step.tool}`)) return await route.continue()
                consumed = true
                sample.injectedArguments = { tool: step.tool, arguments: step.arguments }
                await route.continue({ postData: JSON.stringify({ ...body, arguments: { ...body.arguments, ...step.arguments } }) })
                await page.unroute('**/api/tools/call', intercept)
              } catch (error) {
                sample.errors.push(`Fault injection failed: ${error.message}`)
                await route.abort().catch(() => {})
              }
            }
            await page.route('**/api/tools/call', intercept)
            break
          }
          case 'drag': {
            const source = locator(step)
            await source.scrollIntoViewIfNeeded()
            const from = await source.boundingBox()
            const to = await page.locator(expand(step.target)).boundingBox()
            if (!from || !to) throw new Error('Unframed drag source or destination')
            await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2)
            await page.mouse.down()
            await page.mouse.move(from.x + from.width / 2 + 8, from.y + from.height / 2, { steps: 4 })
            await page.mouse.move(to.x + to.width / 2, to.y + Math.min(35, to.height / 2), { steps: 30 })
            await page.mouse.up()
            break
          }
          case 'media': {
            const preview = host(step.widget).locator('.media-preview').first()
            if (!['ready', 'error', 'empty'].includes(step.state)) throw new Error('Explicit media state required')
            await expect(preview).toBeVisible()
            await expect(preview).toHaveAttribute('data-media-state', step.state)
            await preview.evaluate(node => node.scrollIntoView({ block: 'center', inline: 'nearest' }))
            if (step.state !== 'ready') {
              await expect(preview.getByRole('status')).toBeVisible()
              expect((await preview.getByRole('status').innerText()).trim()).not.toBe('')
              break
            }
            if (!['image', 'video'].includes(step.kind)) throw new Error('Explicit image/video kind required')
            const media = preview.locator(step.kind === 'image' ? 'img' : 'video')
            await expect(media).toBeVisible()
            const frame = await media.boundingBox()
            expect(frame?.width).toBeGreaterThan(30)
            expect(frame?.height).toBeGreaterThan(30)
            expect(frame.x).toBeGreaterThanOrEqual(0)
            expect(frame.y).toBeGreaterThanOrEqual(0)
            expect(frame.x + frame.width).toBeLessThanOrEqual(viewport.width)
            expect(frame.y + frame.height).toBeLessThanOrEqual(viewport.height)
            if (step.kind === 'image') {
              expect(await media.evaluate(node => node.complete && node.naturalWidth > 1 && node.naturalHeight > 1)).toBe(true)
            } else {
              expect(await media.evaluate(node => node.controls && !node.autoplay && node.videoWidth > 1)).toBe(true)
              await media.evaluate(node => node.play())
              const initial = await media.evaluate(node => node.currentTime)
              await expect.poll(() => media.evaluate(node => node.currentTime)).toBeGreaterThan(initial + 0.1)
              await media.evaluate(node => node.pause())
            }
            sample.media = [...(sample.media || []), { step: step.id, kind: step.kind,
              frame, source: await media.evaluate(node => node.currentSrc) }]
            break
          }
          case 'reload': await page.reload({ waitUntil: 'domcontentloaded' }); await ready(); break
          case 'screenshot': await page.screenshot({ path: path.join(output, `${layout}-${step.id}.png`), fullPage: true, animations: 'disabled' }); break
          default: throw new Error(`Unknown journey action: ${step.type}`)
        }
        sample.checks.push({ id: step.id, status: 'passed', durationMs: Date.now() - start })
        sample.active = null
        console.log(`${layout}: ${step.id} passed`)
      }
    } catch (error) {
      sample.failure = error.message
      sample.modalControls = await page.locator('ion-modal ion-button,ion-modal button').evaluateAll(nodes => nodes.map(node => node.outerHTML))
      sample.runtimeDiagnostics = await page.evaluate(() => window.__ADAOS_RUNTIME_DEBUG__?.get?.() ?? null)
    }
    await Promise.all(responses)
    if (expectedRuntimeSource) {
      for (const response of sample.network.filter(item => item.request && item.status >= 200 && item.status < 300)) {
        if (response.runtimeSource !== expectedRuntimeSource) {
          sample.errors.push(`Expected ${expectedRuntimeSource} runtime, received ${response.runtimeSource || 'none'}`)
        }
      }
    }
    sample.text = await page.locator('body').innerText()
    await page.screenshot({ path: path.join(output, `${layout}-final.png`), fullPage: true, animations: 'disabled' })
    await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
    await context.close()
  }
} finally { await browser.close() }
report.passed = report.samples.length === 2 && report.samples.every(sample => !sample.failure && !sample.errors.length)
await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2) + '\n', 'utf8')
console.log(JSON.stringify({ passed: report.passed, samples: report.samples.map(s => ({ layout: s.layout, checks: s.checks.length, failure: s.failure })) }))
if (!report.passed) process.exitCode = 1
