import { addProtocol, getVersion, Map as MapLibreMap, NavigationControl, setWorkerUrl } from '/assets/maplibre-gl.mjs'
import { Protocol } from '/assets/pmtiles.js'

const districtButton = document.querySelector('#district')
const slider = document.querySelector('#year')
const yearLabel = document.querySelector('#snapshot-year')
const playButton = document.querySelector('#play')
const status = document.querySelector('#status')
const hover = document.querySelector('#hover')
const selected = document.querySelector('#selected')
const populationFormat = new Intl.NumberFormat('ru-RU')
const source = 'municipalities'
const fill = 'municipal-fill'
const outline = 'municipal-outline'
const markers = 'city-markers'
const activeOpacity = ['case', ['boolean', ['feature-state', 'active'], false], 1, 0]

setWorkerUrl('/assets/maplibre-gl-worker.mjs')
const protocol = new Protocol()
addProtocol('pmtiles', protocol.tile)
const map = new MapLibreMap({
  container: 'map', center: [39, 55], zoom: 4, renderWorldCopies: false, fadeDuration: 0,
  style: { version: 8, transition: { duration: 0, delay: 0 }, sources: {}, layers: [
    { id: 'background', type: 'background', paint: { 'background-color': '#e9f0ed' } },
  ] },
})
map.addControl(new NavigationControl({ showCompass: false }))

let data
let citiesById
let year = Number(slider.value)
let pinned
let timer
let pending = false
let initialMs
const transitions = []
const errors = []
let painted = new Map()
map.on('error', event => errors.push(event.error.message))
map.once('load', () => { districtButton.disabled = false })

function pick(point) {
  const feature = map.queryRenderedFeatures(point, { layers: [markers, fill] })
    .find(feature => feature.layer.id === markers || inYear(feature.properties))
  if (!feature) return undefined
  return { kind: feature.layer.id === markers ? 'city' : 'municipality', id: feature.properties.id }
}

function inYear(properties) {
  return properties.year_from <= year && year < properties.year_to
}

function renderAnnotation(element, selection) {
  const city = selection?.kind === 'city'
  const row = city ? citiesById.get(selection.id) : data.municipalitiesByYear[year][selection?.id]
  element.hidden = !row
  if (!row) return
  const name = document.createElement('strong')
  name.textContent = row.name
  const description = document.createElement('span')
  description.textContent = city ? `Город · ${row.region}` : row.region
  const value = document.createElement('span')
  const period = city ? data.cityReferencePeriod : `1 января ${year} г.`
  value.textContent = row.population === null ? `Нет оценки на ${period}` : `${populationFormat.format(row.population)} чел. · ${period}`
  element.replaceChildren(name, description, value)
}

function applyPopulation() {
  const rows = Object.entries(data.municipalitiesByYear[year])
  const next = new Map()
  let available = 0
  for (const [id, row] of rows) {
    if (row.population !== null) available += 1
    for (const [start, end] of data.versions[id]) {
      if (!(start <= year && year < end)) continue
      const version = `${id}/${start}/${end}`
      next.set(version, row.population)
      if (painted.get(version) !== row.population) {
        map.setFeatureState({ source, sourceLayer: source, id: version }, { active: true, population: row.population })
      }
    }
  }
  for (const version of painted.keys()) {
    if (!next.has(version)) map.removeFeatureState({ source, sourceLayer: source, id: version })
  }
  painted = next
  map.triggerRepaint()
  status.textContent = `${rows.length} МО · ${data.cities.length} городов · ${available} оценок Росстата`
  yearLabel.value = String(year)
  hover.hidden = true
  if (pinned) renderAnnotation(selected, pinned)
}

async function loadDistrict() {
  const start = performance.now()
  districtButton.disabled = true
  status.textContent = 'Загружаем округ…'
  try {
    const response = await fetch('/data/district.json')
    if (!response.ok) throw new Error(`District data HTTP ${response.status}`)
    data = await response.json()
    citiesById = new Map(data.cities.map(city => [city.id, city]))
    const idle = map.once('idle')
    map.addSource(source, { type: 'vector', url: `pmtiles://${location.origin}/data/district.pmtiles`, promoteId: 'version' })
    const area = { source, 'source-layer': source }
    map.addLayer({ id: fill, type: 'fill', ...area, paint: {
      'fill-color': ['case', ['==', ['feature-state', 'population'], null], '#d6ded9',
        ['interpolate', ['linear'], ['number', ['feature-state', 'population'], 0],
          0, '#deefe4', 20000, '#a1ccb6', 80000, '#4d9c87', 200000, '#0b5c54']],
      'fill-opacity': activeOpacity,
    } })
    map.addLayer({ id: outline, type: 'line', ...area, paint: { 'line-color': '#60887e', 'line-width': 0.7, 'line-opacity': activeOpacity } })
    map.addSource('cities', { type: 'geojson', data: { type: 'FeatureCollection', features: data.cities.map(city => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [city.longitude, city.latitude] },
      properties: { id: city.id, population: city.population },
    })) } })
    map.addLayer({ id: markers, type: 'circle', source: 'cities', paint: {
      'circle-radius': ['*', ['sqrt', ['get', 'population']], 0.029], 'circle-color': '#173c6b',
      'circle-opacity': 0.9, 'circle-stroke-color': '#fff', 'circle-stroke-width': 0.7,
    } })
    applyPopulation()
    map.fitBounds(data.bounds, { padding: 30, duration: 0 })
    await idle
    initialMs = performance.now() - start
    slider.disabled = false
    playButton.disabled = false
  } catch (error) {
    errors.push(error.message)
    status.textContent = 'Не удалось загрузить округ'
  }
}

async function updateYear() {
  const newYear = Number(slider.value)
  if (pending || year === newYear) return
  const start = performance.now()
  pending = true
  year = newYear
  const idle = map.once('idle')
  applyPopulation()
  await idle
  transitions.push({ year, ms: performance.now() - start })
  pending = false
  if (year !== Number(slider.value)) void updateYear()
}

districtButton.addEventListener('click', loadDistrict)
slider.addEventListener('input', updateYear)
playButton.addEventListener('click', () => {
  if (timer) {
    clearInterval(timer)
    timer = undefined
    playButton.textContent = 'Воспроизвести'
    return
  }
  playButton.textContent = 'Пауза'
  timer = setInterval(() => {
    if (pending) return
    slider.value = String(year === Number(slider.max) ? slider.min : year + 1)
    void updateYear()
  }, 350)
})

map.on('mousemove', event => {
  if (!data || initialMs === undefined) return
  const item = pick(event.point)
  map.getCanvas().style.cursor = item ? 'pointer' : ''
  const x = Math.max(0, Math.min(event.point.x + 12, map.getCanvas().clientWidth - 345))
  const y = Math.min(event.point.y + 10, map.getCanvas().clientHeight - 90)
  hover.style.transform = `translate(${x}px, ${y}px)`
  renderAnnotation(hover, item)
})
map.getCanvas().addEventListener('mouseleave', () => { hover.hidden = true })
map.on('click', event => {
  if (initialMs === undefined) return
  pinned = pick(event.point)
  renderAnnotation(selected, pinned)
})

function visibleIds(layer) {
  return new Set(map.queryRenderedFeatures({ layers: [layer] }).map(feature => feature.properties.id))
}

window.federalMapBench = {
  get initialMs() { return initialMs },
  get settled() { return !pending },
  get version() { return getVersion() },
  errors, transitions,
  snapshot: () => {
    const geometry = map.queryRenderedFeatures({ layers: [fill] })
    const visible = new Set(geometry.filter(feature => inYear(feature.properties)).map(feature => feature.properties.id))
    const rows = data.municipalitiesByYear[year]
    return {
      year, zoom: map.getZoom(), sourceTilesLoaded: map.areTilesLoaded(),
      visibleMunicipalities: visible.size, datasetMunicipalities: Object.keys(rows).length,
      visibleCities: visibleIds(markers).size, datasetCities: data.cities.length,
      populationStateMatches: geometry.every(({ properties }) => {
        const state = map.getFeatureState({ source, sourceLayer: source, id: properties.version })
        const active = inYear(properties)
        return Boolean(state.active) === active && (!active || state.population === rows[properties.id]?.population)
      }),
    }
  },
  sample: kind => {
    if (kind === 'city') {
      for (const city of data.cities) {
        const point = map.project([city.longitude, city.latitude])
        if (point.x < 20 || point.y < 20 || point.x > map.getCanvas().clientWidth - 360) continue
        const item = pick(point)
        if (item?.kind === kind && item.id === city.id) return { x: point.x, y: point.y, name: city.name, population: city.population }
      }
    } else {
      const canvas = map.getCanvas()
      for (let y = 40; y < canvas.clientHeight - 40; y += 15) {
        for (let x = 40; x < canvas.clientWidth - 360; x += 15) {
          const item = pick([x, y])
          const row = data.municipalitiesByYear[year][item?.id]
          if (item?.kind === kind && row && row.population !== null) return { x, y, name: row.name, population: row.population }
        }
      }
    }
    throw new Error(`No visible ${kind} to test`)
  },
}
