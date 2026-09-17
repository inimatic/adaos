import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'
import { isDeepStrictEqual } from 'node:util'
import { canonicalWidgetSources } from './widget-source-parity.mjs'

const scenario = process.env.ADAOS_E2E_SCENARIO_ID
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const token = process.env.ADAOS_E2E_HUB_TOKEN
const selectWidget = process.env.ADAOS_E2E_SELECT_WIDGET || ''
const locale = process.env.ADAOS_E2E_LOCALE || 'en'
const emptyMode = process.env.ADAOS_E2E_EMPTY_STATES === '1'
const spaceKind = process.env.ADAOS_E2E_SPACE_KIND || 'development'
const reviewStage = process.env.ADAOS_E2E_REVIEW_STAGE || 'prototype'
const expectTrialUnavailable = process.env.ADAOS_E2E_EXPECT_TRIAL_UNAVAILABLE === '1'
if (expectTrialUnavailable && reviewStage !== 'trial') throw new Error('Trial admission probe requires Trial stage')
if (!['prototype', 'automation', 'trial', 'publication'].includes(reviewStage)) throw new Error('Unsupported review stage')
if (!['development', 'workspace'].includes(spaceKind)) throw new Error('Unsupported review space')
if (emptyMode && spaceKind !== 'development') throw new Error('Empty fixture probes require development space')
const dictionaryProbe = process.env.ADAOS_E2E_DICTIONARY_PROBE === '1'
const expectedWebuiPath = process.env.ADAOS_E2E_EXPECTED_WEBUI
  ? path.resolve(process.env.ADAOS_E2E_EXPECTED_WEBUI) : null
const expectedWebui = expectedWebuiPath
  ? JSON.parse(await fs.readFile(expectedWebuiPath, 'utf8')) : null
let expectedTrialIdentity = null
if (reviewStage === 'trial' && expectedWebuiPath) {
  const trialRoot = path.dirname(path.dirname(path.dirname(expectedWebuiPath)))
  const lock = JSON.parse(await fs.readFile(path.join(trialRoot, '.adaos', 'workspace.lock.json'), 'utf8'))
  const releaseDigest = String(lock?.slots?.primary?.release_digest || '')
  if (!releaseDigest.startsWith('sha256:')) throw new Error('Pinned Trial WorkspaceLock has no primary release digest')
  expectedTrialIdentity = {
    materializationRevision: path.basename(trialRoot),
    sourceFingerprint: `trial:${releaseDigest}`,
  }
}
let expectedTranslation
if (dictionaryProbe) {
  const checkpoint = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CHECKPOINT, 'utf8'))
  const created = checkpoint.steps.find(step => step.id === 'create')?.output
  if (!checkpoint.cleanup?.test || created.scenario_id !== scenario) throw new Error('Dictionary probe requires an owned test checkpoint')
  const dictionary = JSON.parse(await fs.readFile(path.join(created.artifact_root, `assets/i18n/${locale}.json`), 'utf8'))
  expectedTranslation = Object.entries(dictionary).find(([key]) => key.startsWith('value.'))
  if (!expectedTranslation) throw new Error('Dictionary probe needs a value label')
}
let emptyCollections = []
if (emptyMode) {
  const checkpoint = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CHECKPOINT, 'utf8'))
  const created = checkpoint.steps.find(step => step.id === 'create')?.output
  if (!checkpoint.cleanup?.test || created.scenario_id !== scenario) throw new Error('Empty fixture review requires an owned test checkpoint')
  const application = JSON.parse(await fs.readFile(path.join(created.artifact_root, 'webui.json'), 'utf8')).ui.application
  emptyCollections = application.desktop.pageSchema.widgets.filter(widget => ['ui.list', 'ui.table'].includes(widget.type)
    && widget.dataSource?.resourceType?.startsWith('prototype.') && (widget.inputs?.emptyState?.title || widget.inputs?.emptyText))
  if (!emptyCollections.length) throw new Error('No declared empty collection states')
}
if (!['en', 'ru'].includes(locale)) throw new Error('Review locale must be en or ru')
if (!scenario || !webspace || !subnet || !token) {
  throw new Error('Scenario, webspace, subnet and local hub token are required')
}
if (process.env.ENV_TYPE !== 'dev') throw new Error('Prototype review requires ENV_TYPE=dev')
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'artifacts/prototype-review')
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({
  intent: 'webspace.open', zone: 'lo', subnet_id: subnet, webspace_id: webspace,
  space_kind: spaceKind, expected_scenario_id: scenario, try_local_hub: '1',
})) url.searchParams.set(key, value)
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const samples = []
try {
  for (const [layout, viewport] of Object.entries({
    wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 },
  })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace, locale }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({
        adaos_device_id: 'e2e-prototype-review', adaos_webspace_id: webspace,
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1',
        adaos_hub_token: token, adaos_local_subnet_id: subnet,
        adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: locale,
      })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace, locale })
    const page = await context.newPage()
    const emptyChecks = []
    if (emptyMode) {
      await page.route('**/api/resources/query', async route => {
        const resource = route.request().postDataJSON()?.resource_type
        if (!emptyCollections.some(widget => widget.dataSource.resourceType === resource)) return route.continue()
        const response = await route.fetch()
        const body = await response.json()
        if (!response.ok() || !body.ok) return route.fulfill({ response })
        await route.fulfill({ response, json: { ...body, items: [], count: 0, cursor: null, trace: { fixture: 'empty-response-render-test' } } })
      })
    }
    page.setDefaultTimeout(30_000)
    page.setDefaultNavigationTimeout(60_000)
    const errors = []
    const requestFailures = []
    const responseTasks = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('response', response => {
      const target = new URL(response.url())
      if (target.pathname.startsWith('/api/') && response.status() >= 400) {
        const failure = { path: target.pathname, status: response.status() }
        requestFailures.push(failure)
        if (target.pathname.startsWith('/api/resources/')
          || target.pathname === '/api/tools/call'
          || target.pathname === '/api/admin/root_mcp/call'
          || target.pathname === '/v1/root/mcp/call') responseTasks.push(
          response.json().then(body => { failure.detail = body.detail || body.error }).catch(() => {}),
        )
      }
    })
    let failure = null
    const mediaChecks = []
    try {
      console.log(`${layout}: opening ${reviewStage} ${webspace}`)
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync?.materialization?.currentScenario === expected
      }, scenario, { timeout: 60_000 })
      await page.waitForFunction(() => [...document.querySelectorAll('ada-page-widget-host, ada-widget')]
        .some(element => {
          const style = getComputedStyle(element)
          return element.getClientRects().length > 0
            && style.visibility !== 'hidden'
            && style.display !== 'none'
        }), undefined, { timeout: 15_000 })
      await page.evaluate(() => document.fonts.ready)
      console.log(`${layout}: materialization and fonts ready`)
      if (expectedWebui) {
        const expected = canonicalWidgetSources(expectedWebui.ui.application.desktop.pageSchema.widgets)
        const actual = await page.locator('ada-page-widget-host').evaluateAll(elements => elements.map(element => {
          const widget = window.ng?.getComponent(element)?.widget
          return widget ? { id: widget.id, type: widget.type, area: widget.area ?? null, dataSource: widget.dataSource ?? null } : null
        }).filter(Boolean))
        await fs.writeFile(path.join(output, `${layout}-source-parity.json`), JSON.stringify({ expected, actual }, null, 2) + '\n', 'utf8')
        if (!isDeepStrictEqual(canonicalWidgetSources(actual), expected)) throw new Error('Rendered widget sources differ from the pinned WebUI')
      }
      if (reviewStage === 'trial') {
        await page.waitForFunction(expectedIdentity => {
          const source = window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization
          if (!source?.materializationRevision || !/^trial[:_]/.test(source?.sourceFingerprint || '')) return false
          const canonicalFingerprint = value => String(value || '')
            .replace(/^trial[_:]sha256[_:]/, 'trial:sha256:')
          return !expectedIdentity || (
            source.materializationRevision === expectedIdentity.materializationRevision
            && canonicalFingerprint(source.sourceFingerprint)
              === canonicalFingerprint(expectedIdentity.sourceFingerprint)
          )
        }, expectedTrialIdentity, { timeout: 15_000 })
      }
      if (reviewStage !== 'prototype' && !expectTrialUnavailable) {
        await page.waitForFunction(() => {
          const dynamicKinds = new Set(['api', 'mcp', 'resource', 'skill'])
          const visibleDynamicHosts = [...document.querySelectorAll('ada-page-widget-host')]
            .filter(element => {
              const style = getComputedStyle(element)
              if (!element.getClientRects().length || style.visibility === 'hidden' || style.display === 'none') return false
              const component = window.ng?.getComponent(element)
              return dynamicKinds.has(String(component?.widget?.dataSource?.kind || ''))
            })
          return visibleDynamicHosts.every(element => {
            const status = window.ng?.getComponent(element)?.dataSourceStatus
            const state = String(status?.state || '')
            return status?.hasValue === true || ['error', 'unavailable'].includes(state)
          })
        }, undefined, { timeout: 30_000 })
        const failedDataSources = await page.locator('ada-page-widget-host').evaluateAll(elements => {
          const dynamicKinds = new Set(['api', 'mcp', 'resource', 'skill'])
          return elements.flatMap(element => {
            const style = getComputedStyle(element)
            const component = window.ng?.getComponent(element)
            const source = component?.widget?.dataSource
            const status = component?.dataSourceStatus
            if (!element.getClientRects().length || style.visibility === 'hidden' || style.display === 'none'
              || !dynamicKinds.has(String(source?.kind || ''))
              || status?.hasValue === true
              || !['error', 'unavailable'].includes(String(status?.state || ''))) return []
            return [{
              widgetId: component?.widget?.id,
              sourceKind: source?.kind,
              state: status?.state,
              reason: status?.reason,
              diagnostic: component?.dataSourceDiagnosticText,
            }]
          })
        })
        if (failedDataSources.length) {
          throw new Error(`Visible data sources failed: ${JSON.stringify(failedDataSources)}`)
        }
      }
      if (expectTrialUnavailable) {
        await page.waitForFunction(() => {
          const hosts = [...document.querySelectorAll('ada-page-widget-host')]
            .map(node => window.ng?.getComponent(node))
            .filter(component => ['skill', 'api'].includes(component?.widget?.dataSource?.kind))
          return hosts.length > 0 && hosts.every(component => component.dataSourceStatus?.state === 'error'
            && component.dataSourceDiagnosticText.includes('trial_runtime_unavailable'))
        }, undefined, { timeout: 15_000 })
      }
      if (dictionaryProbe) {
        await page.waitForFunction(([key, value]) => {
          const component = window.ng?.getComponent(document.querySelector('ada-table-widget, ada-list-widget, ada-details-widget'))
          return component?.i18n?.t(key) === value
        }, expectedTranslation, { timeout: 30_000 })
      }
      if (emptyMode) {
        for (const widget of emptyCollections) {
          const host = page.locator(`[data-webui-widget-id=${JSON.stringify(widget.id)}]`)
          const title = widget.inputs.emptyState?.title || widget.inputs.emptyText
          await host.getByText(title, { exact: true }).waitFor()
          if (await host.locator('tr.row-selectable, .collection-focus-item').count()) throw new Error('Empty response still renders records')
          emptyChecks.push({ widget: widget.id, status: 'passed', fixtureKind: 'intercepted_empty_response', runtimeMutation: false })
        }
      }
      if (selectWidget && !emptyMode) {
        await page.locator(`[data-webui-widget-id=${JSON.stringify(selectWidget)}]`)
          .locator('tr.row-selectable, .collection-focus-item').first().click()
        await page.waitForFunction(() => [...document.querySelectorAll('ada-details-widget')]
          .some(element => {
            const style = getComputedStyle(element)
            return element.getClientRects().length > 0
              && style.visibility !== 'hidden'
              && style.display !== 'none'
              && !!element.textContent?.trim()
          }), undefined, { timeout: 15_000 })
      }
      if (!emptyMode && ['1', 'inspect'].includes(process.env.ADAOS_E2E_MEDIA_TESTS)) {
        const rows = page.locator(`[data-webui-widget-id=${JSON.stringify(selectWidget)}]`)
          .locator('tr.row-selectable, .collection-focus-item')
        for (let index = 0; index < await rows.count(); index += 1) {
          await rows.nth(index).click()
          const viewer = page.locator('ada-media-preview').first()
          await viewer.waitFor()
          await page.waitForFunction(() => ['ready', 'error', 'empty'].includes(document.querySelector('ada-media-preview [data-media-state]')?.getAttribute('data-media-state')))
          const result = await viewer.evaluate(async element => {
            const media = element.querySelector('video,audio,img')
            const state = element.querySelector('[data-media-state]').getAttribute('data-media-state')
            if (state === 'ready' && media instanceof HTMLMediaElement) {
              await media.play()
              await new Promise((resolve, reject) => {
                if (media.currentTime > 0) return resolve()
                const timer = setTimeout(() => reject(new Error('Media clock did not advance')), 10_000)
                media.addEventListener('timeupdate', () => { clearTimeout(timer); resolve() }, { once: true })
              })
              media.pause()
            }
            return { state, kind: media?.tagName, currentTime: media?.currentTime, naturalWidth: media?.naturalWidth }
          })
          mediaChecks.push({ row: index, ...result })
        }
        if (process.env.ADAOS_E2E_MEDIA_TESTS === '1' && (!mediaChecks.some(item => item.kind === 'VIDEO' && item.currentTime > 0)
          || !mediaChecks.some(item => item.kind === 'IMG' && item.naturalWidth > 0)
          || !mediaChecks.some(item => item.state === 'error'))) throw new Error('Image, playing video and unavailable-media coverage required')
      }
    } catch (error) { failure = error.message }
    const text = await page.locator('body').innerText()
    const geometry = await page.evaluate(() => ({
      viewportWidth: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      currentScenario: window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario,
      materialization: window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization,
      widgets: document.querySelectorAll('ada-page-widget-host, ada-widget').length,
      loadingIndicators: document.querySelectorAll('ion-spinner').length,
      dataSources: [...document.querySelectorAll('ada-page-widget-host')].flatMap(element => {
        const style = getComputedStyle(element)
        const component = window.ng?.getComponent(element)
        const source = component?.widget?.dataSource
        if (!source || !element.getClientRects().length || style.visibility === 'hidden' || style.display === 'none') return []
        return [{
          widgetId: component?.widget?.id,
          sourceKind: source?.kind,
          state: component?.dataSourceStatus?.state,
          hasValue: component?.dataSourceStatus?.hasValue,
        }]
      }),
      tables: document.querySelectorAll('ada-table-widget').length,
      language: document.documentElement.lang,
      dictionary: (() => {
        const component = window.ng?.getComponent(document.querySelector('ada-table-widget, ada-list-widget, ada-details-widget'))
        const i18n = component?.i18n
        return { language: i18n?.getLang(), revision: i18n?.revision,
          valueLabels: Object.keys(i18n?.activeDict || {}).filter(key => key.startsWith('value.')).length }
      })(),
      media: Array.from(document.querySelectorAll('ada-page-widget-host img, ada-page-widget-host video, ada-page-widget-host audio')).map(element => ({
        kind: element.tagName, src: element.currentSrc || element.getAttribute('src'),
        naturalWidth: element.naturalWidth, readyState: element.readyState,
      })),
    }))
    await page.screenshot({ path: path.join(output, `${layout}.png`), fullPage: true })
    const scrollSurfaces = await page.evaluate(() => {
      const elements = []
      const visit = root => {
        for (const element of root.querySelectorAll('*')) {
          if (element.shadowRoot) visit(element.shadowRoot)
          const style = getComputedStyle(element)
          if (element.clientHeight > 150 && element.scrollHeight > element.clientHeight + 2
            && /auto|scroll/.test(style.overflowY)) {
            elements.push(element)
          }
        }
      }
      visit(document)
      return elements.slice(0, 8).map(element => {
        const result = { tag: element.tagName, height: element.clientHeight, scrollHeight: element.scrollHeight }
        element.scrollTop = element.scrollHeight
        return result
      })
    })
    if (scrollSurfaces.length) {
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
      await page.screenshot({ path: path.join(output, `${layout}-bottom.png`), fullPage: true })
    }
    await Promise.allSettled(responseTasks)
    samples.push({ layout, viewport, locale, selectWidget, geometry, mediaChecks, emptyChecks, scrollSurfaces, failure, errors, requestFailures, text })
    await context.close()
  }
} finally { await browser.close() }
await fs.writeFile(path.join(output, 'review.json'), JSON.stringify({
  stage: reviewStage, scenario, webspace, url: url.href, samples,
  note: 'Screenshots and diagnostics are review evidence, not an automatic approval.',
}, null, 2) + '\n', 'utf8')
console.log(JSON.stringify(samples.map(({ text, ...sample }) => sample), null, 2))
if (samples.some(sample => sample.failure || sample.errors.length || sample.requestFailures.some(
  failure => failure.path.startsWith('/api/resources/')
    || (reviewStage !== 'prototype' && failure.path === '/api/tools/call'),
))) process.exitCode = 1
