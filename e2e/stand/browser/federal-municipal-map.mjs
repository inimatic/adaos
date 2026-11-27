import assert from 'node:assert/strict'
import { createReadStream } from 'node:fs'
import { readFile, stat, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'
import { chromium, expect } from '@playwright/test'

const [outputArgument, profileArgument] = process.argv.slice(2)
if (!outputArgument || !profileArgument) throw new Error('Usage: node federal-municipal-map.mjs <artifact directory> <profile directory | --serve>')
const serve = profileArgument === '--serve'
const output = resolve(outputArgument)
const fixtures = join(dirname(fileURLToPath(import.meta.url)), 'fixtures')
const modules = resolve(fixtures, '../../../../src/adaos/integrations/adaos-client/node_modules')
const maplibre = join(modules, 'maplibre-gl/dist')

const archive = join(output, 'district.pmtiles')
const archiveInfo = await stat(archive)
const json = await readFile(join(output, 'district.json'))
const data = JSON.parse(json)
const coverage = JSON.parse(await readFile(join(output, 'coverage.json'), 'utf8'))
const statsBody = gzipSync(json)
const javascript = 'text/javascript; charset=utf-8'
const assets = new Map([
  ['/', [join(fixtures, 'federal-municipal-map.html'), 'text/html; charset=utf-8']],
  ['/map.js', [join(fixtures, 'federal-municipal-map.mjs'), javascript]],
  ['/assets/maplibre-gl.css', [join(maplibre, 'maplibre-gl.css'), 'text/css; charset=utf-8']],
  ['/assets/maplibre-gl.mjs', [join(maplibre, 'maplibre-gl.mjs'), javascript]],
  ['/assets/maplibre-gl-worker.mjs', [join(maplibre, 'maplibre-gl-worker.mjs'), javascript]],
  ['/assets/maplibre-gl-shared.mjs', [join(maplibre, 'maplibre-gl-shared.mjs'), javascript]],
  ['/assets/pmtiles.js', [join(modules, 'pmtiles/dist/esm/index.js'), javascript]],
  ['/assets/fflate.js', [join(modules, 'fflate/esm/browser.js'), javascript]],
])
const dataResponses = []

const server = createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1').pathname
  if (request.method !== 'GET') return response.writeHead(405).end()
  if (url === '/data/district.pmtiles') {
    const range = /^bytes=(\d+)-(\d+)$/.exec(request.headers.range ?? '')
    const start = range ? Number(range[1]) : 0
    const end = range ? Math.min(Number(range[2]), archiveInfo.size - 1) : archiveInfo.size - 1
    if (start > end) return response.writeHead(416, { 'Content-Range': `bytes */${archiveInfo.size}` }).end()
    const status = range ? 206 : 200
    const bytes = end - start + 1
    if (!serve) response.once('finish', () => dataResponses.push({ url, status, bytes }))
    response.writeHead(status, {
      'Content-Type': 'application/octet-stream', 'Content-Length': bytes, 'Accept-Ranges': 'bytes',
      ETag: `"${archiveInfo.size}-${Math.round(archiveInfo.mtimeMs)}"`,
      ...(range ? { 'Content-Range': `bytes ${start}-${end}/${archiveInfo.size}` } : {}),
    })
    return createReadStream(archive, { start, end }).on('error', () => response.destroy()).pipe(response)
  }
  if (url === '/data/district.json') {
    if (!serve) response.once('finish', () => dataResponses.push({ url, status: 200, bytes: statsBody.length }))
    return response.writeHead(200, {
      'Content-Type': 'application/json', 'Content-Encoding': 'gzip', 'Content-Length': statsBody.length,
    }).end(statsBody)
  }
  if (url === '/favicon.ico') return response.writeHead(204).end()
  const asset = assets.get(url)
  if (!asset) return response.writeHead(404).end()
  response.writeHead(200, { 'Content-Type': asset[1] })
  createReadStream(asset[0]).on('error', () => response.destroy()).pipe(response)
})

await new Promise((resolve, reject) => {
  server.once('error', reject)
  server.listen(serve ? 8101 : 0, '127.0.0.1', resolve)
})
const origin = `http://127.0.0.1:${server.address().port}`
if (serve) {
  console.log(origin)
} else {
  await checkBrowser()
}

function timing(samples) {
  const sorted = [...samples].sort((a, b) => a - b)
  const percentile = p => Math.round(sorted[Math.ceil(sorted.length * p) - 1] * 10) / 10
  return { samples: sorted.length, p50Ms: percentile(0.5), p95Ms: percentile(0.95), maxMs: percentile(1) }
}

function assertSnapshot(snapshot) {
  assert(snapshot.sourceTilesLoaded, 'Map sources did not settle')
  assert(snapshot.populationStateMatches, 'Visible shapes did not receive their actual population for the year')
  assert.equal(snapshot.datasetMunicipalities, coverage.annualCoverage[snapshot.year].municipalities)
  assert.equal(snapshot.visibleMunicipalities, snapshot.datasetMunicipalities, 'Expected every municipal geometry in the district viewport')
  assert.equal(snapshot.datasetCities, coverage.districtCities)
  assert.equal(snapshot.visibleCities, coverage.districtCities, 'Expected all registry cities in the district viewport')
}

async function checkBrowser() {
  let context
  let page
  const requests = []
  const report = {
    passed: false, coverage, viewport: { width: 1440, height: 920 }, transport: 'loopback HTTP with Range',
    rendering: 'Chromium SwiftShader', errors: [], snapshots: [],
  }
  try {
    const registry = JSON.parse(await readFile(join(output, 'city-registry.json'), 'utf8'))
    assert.equal(registry.cities.length, coverage.registryCities)
    assert.deepEqual(registry.cities.filter(city => city.district === coverage.district), data.cities)
    context = await chromium.launchPersistentContext(resolve(profileArgument), {
      headless: true, viewport: report.viewport, locale: 'ru-RU',
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
    })
    page = context.pages()[0]
    page.on('pageerror', error => report.errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') report.errors.push(message.text()) })
    page.on('request', request => {
      const url = new URL(request.url())
      if (url.pathname.startsWith('/data/')) requests.push(url.pathname)
    })
    await page.goto(origin, { waitUntil: 'load' })
    await expect(page.locator('#district')).toBeEnabled({ timeout: 20000 })
    assert.deepEqual(requests, [], 'District detail fetched before selecting the district')
    await page.locator('#district').click()
    await expect(page.locator('#year')).toBeEnabled({ timeout: 30000 })
    const initial = await page.evaluate(() => ({
      mapLibre: window.federalMapBench.version, ms: window.federalMapBench.initialMs, snapshot: window.federalMapBench.snapshot(),
    }))
    assertSnapshot(initial.snapshot)
    assert.equal(initial.snapshot.year, 2025)
    report.browser = await page.evaluate(() => navigator.userAgent)
    report.mapLibre = initial.mapLibre
    report.initialLoad = {
      ms: Math.round(initial.ms * 10) / 10, snapshot: initial.snapshot,
      requests: requests.length, stats: dataResponses.filter(response => response.url.endsWith('.json')),
      tileRanges: dataResponses.filter(response => response.url.endsWith('.pmtiles')),
    }
    assert.equal(report.initialLoad.stats.length, 1)
    assert(report.initialLoad.tileRanges.length > 0)
    assert(report.initialLoad.tileRanges.every(response => response.status === 206), 'Expected HTTP 206 for each tile range')
    report.initialLoad.archiveTransferredBytes = report.initialLoad.tileRanges.reduce((sum, response) => sum + response.bytes, 0)
    report.initialLoad.totalDataBytes = report.initialLoad.archiveTransferredBytes + statsBody.length
    assert(report.initialLoad.archiveTransferredBytes < coverage.archiveBytes, 'Initial viewport downloaded the full archive')
    await page.screenshot({ path: join(output, 'central-2025.png') })

    const initialRequests = requests.length
    const initialZoom = initial.snapshot.zoom
    const years = Object.keys(coverage.annualCoverage).map(Number)
    for (let iteration = 0; iteration < 3; iteration += 1) {
      for (const year of years) {
        const snapshot = await page.evaluate(async year => {
          const bench = window.federalMapBench
          const input = document.querySelector('#year')
          const transition = bench.transitions.length
          input.value = String(year)
          input.dispatchEvent(new Event('input', { bubbles: true }))
          const deadline = performance.now() + 5000
          while (bench.transitions.length === transition) {
            if (performance.now() > deadline) throw new Error(`Map did not settle for ${year}`)
            await new Promise(requestAnimationFrame)
          }
          return { ...bench.snapshot(), ms: bench.transitions.at(-1).ms }
        }, year)
        assert.equal(snapshot.year, year)
        assert.equal(snapshot.zoom, initialZoom)
        assertSnapshot(snapshot)
        report.snapshots.push(snapshot)
      }
    }
    report.yearSwitch = timing(report.snapshots.map(value => value.ms))
    report.yearSwitch.dataRequests = requests.length - initialRequests
    assert.equal(report.yearSwitch.dataRequests, 0, 'Changing year at fixed viewport fetched new data')

    await page.locator('#play').click()
    await page.waitForFunction(() => window.federalMapBench.transitions.length >= 26)
    await page.locator('#play').click()
    await page.waitForFunction(() => window.federalMapBench.settled)
    report.animation = await page.evaluate(() => window.federalMapBench.transitions.slice(24))
    assert.deepEqual(report.animation.slice(0, 2).map(value => value.year), [2018, 2019])
    assert.equal(requests.length - initialRequests, 0, 'Animation at fixed viewport fetched new data')

    const map = page.locator('#map')
    report.annotations = []
    for (const kind of ['city', 'municipality']) {
      const sample = await page.evaluate(kind => window.federalMapBench.sample(kind), kind)
      const position = { x: sample.x, y: sample.y }
      const value = new Intl.NumberFormat('ru-RU').format(sample.population)
      const period = kind === 'city' ? coverage.cityReferencePeriod : `1 января ${report.animation.at(-1).year} г.`
      const hover = page.locator('#hover')
      const selected = page.locator('#selected')
      await map.hover({ position })
      await expect(hover).toContainText(sample.name)
      await expect(hover).toContainText(value)
      await expect(hover).toContainText(period)
      await map.click({ position })
      await expect(selected).toContainText(sample.name)
      await expect(selected).toContainText(value)
      await expect(selected).toContainText(period)
      report.annotations.push({ kind, name: sample.name, population: sample.population, period })
    }
    await page.screenshot({ path: join(output, 'central-municipality.png') })
    report.errors.push(...await page.evaluate(() => window.federalMapBench.errors))
    assert.deepEqual(report.errors, [])
    report.passed = true
    console.log(JSON.stringify({
      passed: report.passed, mapLibre: report.mapLibre, initialMs: report.initialLoad.ms,
      dataRequests: report.initialLoad.requests, archiveTransferredBytes: report.initialLoad.archiveTransferredBytes,
      statisticsBytes: statsBody.length, initial: initial.snapshot, yearSwitch: report.yearSwitch,
    }))
  } catch (error) {
    report.failure = error.stack ?? String(error)
    if (page) {
      report.errors.push(...await page.evaluate(() => window.federalMapBench?.errors ?? []).catch(() => []))
      await page.screenshot({ path: join(output, 'failure.png') }).catch(() => {})
    }
    throw error
  } finally {
    await context?.close()
    await new Promise(resolve => server.close(resolve))
    await writeFile(join(output, 'load-report.json'), JSON.stringify(report, null, 2))
  }
}
