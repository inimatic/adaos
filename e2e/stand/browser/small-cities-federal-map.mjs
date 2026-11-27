import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { chromium, expect } from '@playwright/test'
import { dismissSystemOverlays, installSystemOverlayDismissal } from './prototype-navigation.mjs'

const { ADAOS_E2E_HUB_URL: hub, ADAOS_E2E_HUB_TOKEN: token, ADAOS_E2E_BROWSER_PROFILE: profile,
  ADAOS_E2E_OUTPUT: output, ENV_TYPE: environment } = process.env
if (environment !== 'dev' || !hub || !token || !profile || !output) throw new Error('Local DEV and artifact paths are required')
const client = 'http://127.0.0.1:8100'
const webspace = 'small-cities-map-review-dev'
const scenario = 'small_cities_analytics'
const assetPath = '/files/content/federal-map-v1/'
const report = { passed: false, revision: '008', checks: {}, mapRequests: [], errors: [] }
await mkdir(output, { recursive: true })
const context = await chromium.launchPersistentContext(profile, {
  headless: true, viewport: { width: 1440, height: 960 }, locale: 'ru-RU',
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--disk-cache-size=10485760'],
})
await context.addInitScript(({ hub, token, webspace }) => {
  Object.assign(window, { __ADAOS_DEBUG__: true, __ADAOS_BASE__: hub, __ADAOS_TOKEN__: token })
  const initial = {
    adaos_device_id: 'e2e-small-cities-municipal-map-20261123', adaos_webspace_id: webspace, adaos_lang: 'ru',
    adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
    adaos_local_subnet_id: 'sn_6acf0c01', adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo',
  }
  for (const [key, value] of Object.entries(initial)) localStorage.setItem(key, value)
}, { hub, token, webspace })
const page = context.pages()[0]
const widget = id => page.locator(`[data-webui-widget-id="${id}"]`).filter({ visible: true }).first()
const widgetComponent = 'ada-federal-municipal-map-widget'
let slow = true
let failNextVolgaHeader = true
await installSystemOverlayDismissal(page)
await context.route('**/*', async route => {
  const request = route.request()
  const url = new URL(request.url())
  if (![client, new URL(hub).origin].includes(url.origin)) return route.abort()
  if (url.pathname.includes(assetPath) && request.method() === 'GET') {
    if (slow && url.pathname.endsWith('/central/district.json')) await new Promise(resolve => setTimeout(resolve, 600))
    if (slow && url.pathname.endsWith('/central/district.pmtiles')) await new Promise(resolve => setTimeout(resolve, 850))
    if (failNextVolgaHeader && url.pathname.endsWith('/volga/district.pmtiles')) {
      failNextVolgaHeader = false
      return route.fulfill({ status: 503, headers: { 'Access-Control-Allow-Origin': client } })
    }
  }
  return route.continue()
})
page.on('pageerror', error => report.errors.push(error.message))
page.on('response', response => {
  const path = new URL(response.url()).pathname
  const idx = path.indexOf(assetPath)
  if (idx >= 0 && response.request().method() === 'GET') report.mapRequests.push({
    asset: path.slice(idx + assetPath.length), status: response.status(), range: response.request().headers()['range'],
  })
})

try {
  const entry = new URL(client)
  entry.search = new URLSearchParams({ webspace, zone: 'lo', try_local_hub: '1', runtime_debug: '1' })
  await page.goto(entry.href, { waitUntil: 'commit' })
  await widget('overview-mode').waitFor({ timeout: 180000 })
  const identity = await page.locator('app-root').evaluate(element => {
    const root = window.ng.getComponent(element)
    return { scenario: root.currentScenario, revision: root.ydoc.getMaterializationSnapshot()?.materializationRevision }
  })
  assert.deepEqual(identity, { scenario, revision: report.revision })
  await dismissSystemOverlays(page)
  await widget('overview-mode').getByText('Границы МО', { exact: true }).click()
  const map = widget('overview-municipal-map').locator(widgetComponent)
  const districts = map.locator('select')
  const progress = map.getByRole('progressbar', { name: 'Загрузка карты' })
  const slider = map.getByRole('slider')
  await expect(districts.locator('option')).toHaveCount(9, { timeout: 45000 })
  await expect(progress).toHaveCount(0)
  await expect.poll(() => map.evaluate(element => {
    const engine = window.ng.getComponent(element).map
    return new Set(engine.queryRenderedFeatures({ layers: ['federal-district-fill'] }).map(feature => feature.properties.id)).size
  }), { timeout: 15000 }).toBe(8)
  assert.deepEqual(report.mapRequests.map(request => request.asset), ['catalog.json'])
  report.checks.countryOverview = { districtCount: 8, assets: ['catalog.json'] }
  await map.screenshot({ path: join(output, 'country.png') })

  await districts.selectOption('central')
  await expect(map.locator('.federal-municipal-map__loading').getByRole('status')).toContainText('Загружаем статистику округа')
  await expect(slider).toBeDisabled()
  await expect(map.getByText(/Получено частей [1-9]\d* из [2-9]\d*/)).toBeVisible()
  await expect(progress).toBeVisible()
  await map.screenshot({ path: join(output, 'district-loading.png') })
  await expect(slider).toBeEnabled({ timeout: 45000 })
  await expect(progress).toHaveCount(0)
  await expect(map).toContainText('627 МО · 259 городов')
  const central = report.mapRequests.filter(request => request.asset.startsWith('central/'))
  const ranges = central.filter(request => request.asset.endsWith('.pmtiles'))
  assert.equal(central.filter(request => request.asset.endsWith('.json')).length, 1)
  assert(ranges.length > 0 && ranges.every(request => request.status === 206 && request.range))
  report.checks.loadingProgress = { statistics: true, ranges: ranges.length, readyAfterRender: true }
  slow = false
  await map.screenshot({ path: join(output, 'central-2025.png') })

  const before = report.mapRequests.length
  for (const [keys, year, municipalityCount] of [[['Home'], 2018, 661], [['End', 'ArrowLeft'], 2024, 645], [['End'], 2025, 627]]) {
    for (const key of keys) await slider.press(key)
    await expect(slider).toHaveAttribute('aria-valuetext', String(year))
    await expect(map).toContainText(`${municipalityCount} МО · 259 городов`)
  }
  await map.getByRole('button', { name: 'Воспроизвести' }).click()
  await expect(slider).toHaveAttribute('aria-valuetext', '2018')
  await map.getByRole('button', { name: 'Пауза' }).click()
  assert.equal(report.mapRequests.length, before)
  report.checks.years = { checked: [2018, 2024, 2025], requests: 0, playback: true }

  const sample = async kind => map.evaluate((element, kind) => {
    const component = window.ng.getComponent(element)
    const map = component.map
    if (kind === 'city') {
      const city = [...component.cities.values()].find(point => point.name === '\u041c\u0430\u043d\u0442\u0443\u0440\u043e\u0432\u043e')
      const { x, y } = map.project([city.longitude, city.latitude])
      return { x, y, name: city.name }
    }
    const canvas = map.getCanvas()
    for (let y = 50; y < canvas.clientHeight - 150; y += 13) {
      for (let x = 40; x < canvas.clientWidth - 50; x += 13) {
        const hit = component.hitAt([x, y])
        if (hit?.kind === kind) return { x, y, name: component.annotation(hit).name }
      }
    }
    throw new Error(`No sample of ${kind} rendered`)
  }, kind)
  const canvas = map.locator('.maplibregl-canvas')
  const card = map.locator('.federal-municipal-map__annotation')
  for (const kind of ['municipality', 'city']) {
    const { x, y, name } = await sample(kind)
    await canvas.hover({ position: { x, y } })
    await expect(map.getByRole('tooltip')).toContainText(name)
    await canvas.click({ position: { x, y } })
    await expect(card).toContainText(name)
    report.checks[kind] = name
  }
  await map.screenshot({ path: join(output, 'central-city.png') })

  await districts.selectOption('volga')
  await expect(map.getByRole('alert')).toContainText('Не удалось загрузить границы округа.', { timeout: 45000 })
  await map.getByRole('button', { name: 'Повторить' }).click()
  await expect(slider).toBeEnabled({ timeout: 45000 })
  await expect(map).toContainText('169 городов')
  report.checks.retry = { district: 'volga', recovered: true }
  await districts.selectOption('')
  await expect(slider).toHaveCount(0)
  await expect(districts).toHaveValue('')
  report.checks.countryReturn = true

  await districts.selectOption('central')
  await expect(slider).toBeEnabled({ timeout: 45000 })
  const { x, y } = await sample('city')
  await canvas.click({ position: { x, y } })
  await card.getByRole('button', { name: 'Паспорт города' }).click()
  await expect(widget('passport-city')).toContainText('2018, 2023', { timeout: 45000 })
  report.checks.passport = true

  await widget('navigation').locator('[data-command-id="overview"]').click()
  await widget('overview-mode').getByText('Карта', { exact: true }).click()
  await expect(widget('overview-map')).toBeVisible()
  report.checks.originalMap = true
  assert.deepEqual(report.errors, [])
  report.passed = true
} catch (error) {
  report.failure = error.stack ?? String(error)
  report.runtime = await page.locator('app-root').evaluate(element => {
    const root = window.ng?.getComponent(element)
    const snapshot = root?.ydoc?.getMaterializationSnapshot()
    const sync = root?.ydoc?.syncRuntime$?.value
    return { scenario: root?.currentScenario, revision: snapshot?.materializationRevision,
      ready: sync?.materializationReady, providerSynced: sync?.providerSynced }
  }, undefined, { timeout: 3000 }).catch(() => undefined)
  await page.screenshot({ path: join(output, 'failed.png') }).catch(() => {})
  throw error
} finally {
  await writeFile(join(output, 'live-report.json'), JSON.stringify(report, null, 2))
  await context.close()
}
