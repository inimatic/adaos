import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'

const scenario = process.env.ADAOS_E2E_SCENARIO_ID
const webspace = process.env.ADAOS_E2E_WEBSPACE_ID
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const token = process.env.ADAOS_E2E_HUB_TOKEN
const selectWidget = process.env.ADAOS_E2E_SELECT_WIDGET || ''
const locale = process.env.ADAOS_E2E_LOCALE || 'en'
const emptyMode = process.env.ADAOS_E2E_EMPTY_STATES === '1'
const dictionaryProbe = process.env.ADAOS_E2E_DICTIONARY_PROBE === '1'
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
  space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1',
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
        if (target.pathname.startsWith('/api/resources/')) responseTasks.push(
          response.json().then(body => { failure.detail = body.detail || body.error }).catch(() => {}),
        )
      }
    })
    let failure = null
    const mediaChecks = []
    try {
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync?.materialization?.currentScenario === expected
      }, scenario, { timeout: 60_000 })
      await page.locator('ada-page-widget-host, ada-widget').first().waitFor({ timeout: 15_000 })
      await page.evaluate(() => document.fonts.ready)
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
        await page.locator('ada-details-widget .details-row').first().waitFor({ timeout: 15_000 })
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
      widgets: document.querySelectorAll('ada-page-widget-host, ada-widget').length,
      loadingIndicators: document.querySelectorAll('ion-spinner').length,
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
  stage: 'prototype', scenario, webspace, url: url.href, samples,
  note: 'Screenshots and diagnostics are review evidence, not an automatic approval.',
}, null, 2) + '\n', 'utf8')
console.log(JSON.stringify(samples.map(({ text, ...sample }) => sample), null, 2))
if (samples.some(sample => sample.failure || sample.errors.length || sample.requestFailures.some(
  failure => failure.path.startsWith('/api/resources/'),
))) process.exitCode = 1
