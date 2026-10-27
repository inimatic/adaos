// Trusted fixture journey: real Client + unchanged application + owned SQLite.
// Core application admission and provider image quality require separate trials.
import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import crypto from 'node:crypto'
import { isExpectedFixtureRestartReadError, fetchOwnedReadWithOneRetry, observePending } from './controlled-faults.mjs'

const expect = baseExpect.configure({ timeout: 30_000 })
const env = process.env
const layout = env.ADAOS_E2E_LAYOUT
const variant = env.ADAOS_E2E_RETOUCH_VARIANT || 'exact'
if (!['wide', 'compact'].includes(layout)) throw Error('Explicit wide/compact layout required')
if (!['exact', 'edited'].includes(variant)) throw Error('Unknown refusal retry variant')
const fixtureRoot = path.resolve(env.ADAOS_E2E_RETOUCH_FIXTURE)
const scratch = (await fs.realpath('.tmp')) + path.sep
if (!fixtureRoot.startsWith(scratch)) throw Error('Owned fixture must be under repository .tmp')
const endpoint = JSON.parse(await fs.readFile(path.join(fixtureRoot, 'fixture-endpoint.json'), 'utf8'))
const marker = JSON.parse(await fs.readFile(path.join(fixtureRoot, 'owned-fixture.json'), 'utf8'))
if (!marker.test || marker.source_digest !== endpoint.source_digest) throw Error('Fixture source ownership mismatch')
const fixtureToken = (await fs.readFile(endpoint.token_file, 'utf8')).trim()
const hub = env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const subnet = env.ADAOS_E2E_SUBNET_ID || 'sn_6acf0c01'
const scenario = env.ADAOS_E2E_SCENARIO_ID || 'natural_retouch'
const webspace = env.ADAOS_E2E_WEBSPACE_ID || 'desktop-dev'
const expectedMaterialization = JSON.parse(env.ADAOS_E2E_EXPECTED_MATERIALIZATION || '{}')
const clientUrl = new URL(env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
  webspace_id: webspace, expected_scenario_id: scenario, try_local_hub: '1',
  ...(env.ADAOS_E2E_SPACE_KIND === 'development' ? { space_kind: 'development' } : {}) })) clientUrl.searchParams.set(key, value)
const token = env.ADAOS_E2E_HUB_TOKEN
const output = path.resolve(env.ADAOS_E2E_OUTPUT)
if (!token || !output.startsWith((await fs.realpath('e2e/artifacts')) + path.sep)) throw Error('Explicit local token and E2E artifact output required')
await fs.mkdir(output, { recursive: true })
const hash = value => 'sha256:' + crypto.createHash('sha256').update(typeof value === 'string' || Buffer.isBuffer(value) ? value : JSON.stringify(value)).digest('hex')
const fixture = async (pathname, options = {}) => {
  const response = await fetch(endpoint.base_url + pathname, { ...options,
    // The fixture is deliberately restarted during a journey. Use a fresh
    // control connection so a pooled socket cannot outlive that process.
    headers: { 'X-Retouch-Fixture-Token': fixtureToken, ...(options.headers || {}), connection: 'close' } })
  if (!response.ok) throw Error(`Fixture ${pathname}: HTTP ${response.status}`)
  return response
}
const audit = async () => (await fixture('/audit')).json()
const specification = {
  schema: 'adaos.e2e.retouch_journey_spec.v1', layout, variant, source_digest: endpoint.source_digest,
  runner_digest: hash(await fs.readFile(new URL(import.meta.url), 'utf8')),
  scope: 'Synthetic Broker/blob/context boundaries; real Client, handler validation, SQLite, CPU masks/exports. No provider quality or Core admission claim.',
  obligations: ['loading-error-empty','upload-jpeg-lossless','bounded-thumbnail-picker','local-draft-preservation','goal-parameters-no-submit','rectangle-protection','budget-admission-failure-visible','atomic-start-goal-scope','automatic-progress-reopen-idempotence','completed-run-cost-authority','compare-zoom-pan-difference-download','adopt-incremented-original','completed-mask-new-run','result-input-history','cancel-and-terminal-recovery'],
}
await fs.writeFile(path.join(output, 'verification-spec.json'), JSON.stringify(specification, null, 2))
const report = { schema: 'adaos.e2e.retouch_journey.v1', specification_digest: hash(specification),
  specification, started_at: new Date().toISOString(), checks: [], errors: [], calls: [], downloads: [], passed: false }
const before = await audit()
if (before.jobs.length || before.runs.length) throw Error('A fresh fixture is required for each layout/trial')
const browser = await chromium.launch({ headless: true })
const viewport = layout === 'wide' ? { width: 1440, height: 1000 } : { width: 390, height: 844 }
const context = await browser.newContext({ viewport })
report.browser_environment = { browser_version: browser.version(), viewport,
  node_version: process.version, platform: process.platform, architecture: process.arch }
await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
await context.addInitScript(({ hub, token, subnet, clientOrigin }) => {
  // This context also visits about:blank to prove background completion.
  // Bootstrap belongs only to the Client, not to opaque or foreign documents.
  if (window.location.origin !== clientOrigin) return
  window.__ADAOS_DEBUG__ = true; window.__ADAOS_BASE__ = hub; window.__ADAOS_TOKEN__ = token
  for (const [key, value] of Object.entries({ adaos_device_id: 'owned-retouch-fixture', adaos_hub_base: hub,
    adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token, adaos_local_subnet_id: subnet,
    adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, value)
}, { hub, token, subnet, clientOrigin: clientUrl.origin })
let initialReadRelease, firstPhotoRead = false, photoReadFailure = true, restartWindow = false
const initialRead = new Promise(resolve => { initialReadRelease = resolve })
await context.route('**/api/tools/**', async route => {
  const request = route.request(), url = new URL(request.url())
  let body
  try { body = request.postDataJSON() } catch {}
  const isTool = url.pathname === '/api/tools/call' && body?.tool?.startsWith('natural_retouch_skill:')
  const isAttachment = url.pathname.startsWith('/api/tools/natural_retouch_skill/')
  if (url.pathname === '/api/tools/call' && !isTool) {
    report.errors.push('Fixture refused a tool outside the admitted Natural Retouch owner')
    return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'fixture_tool_owner_mismatch' }) })
  }
  if (!isTool && !isAttachment) return route.continue()
  if (isTool && body.tool.endsWith(':query_records') && body.arguments?.kind === 'photos' && photoReadFailure) {
    firstPhotoRead = true; await initialRead
    report.injected_read_failure = {status: 400, reason: 'synthetic_initial_read_failure'}
    return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'synthetic_initial_read_failure' }) })
  }
  const start = Date.now()
  try {
    const headers = { ...request.headers(), 'X-Retouch-Fixture-Token': fixtureToken }
    delete headers['x-adaos-token']; delete headers.authorization; delete headers.cookie
    const response = await fetchOwnedReadWithOneRetry(
      () => route.fetch({ url: endpoint.base_url + url.pathname + url.search, headers, timeout: 60_000 }),
      { method: request.method(), tool: body?.tool }, fault => {
        report.route_retries ||= []
        report.route_retries.push({ ...fault, message: fault.message.replaceAll(fixtureToken, '[redacted]'),
          path: url.pathname, kind: 'owned_read_connection_reset', maximum_retries: 1 })
      })
    const bytes = await response.body()
    report.calls.push({ path: url.pathname, tool: body?.tool, command: body?.arguments?.command,
      request_digest: body ? hash(body) : null, status: response.status(), duration_ms: Date.now() - start,
      runtime_source: response.headers()['x-adaos-runtime-source'] })
    await route.fulfill({ response, body: bytes })
  } catch (error) {
    // Do not retain Playwright's verbose call log: it contains transport headers.
    const message = String(error.message).split('\n')[0].replaceAll(fixtureToken, '[redacted]')
    const fault = { restartWindow, message, method: request.method(), tool: body?.tool }
    report.route_failures ||= []; report.route_failures.push({ ...fault, path: url.pathname, command: body?.arguments?.command })
    if (isExpectedFixtureRestartReadError(fault)) {
      report.expected_faults ||= []
      report.expected_faults.push({ kind: 'owned_fixture_restart', path: url.pathname,
        tool: body?.tool, command: body?.arguments?.command, message })
    } else report.errors.push(`Fixture route failed: ${message}`)
    await route.abort() // Never fall back to a real provider tool.
  }
})
const page = await context.newPage(); page.setDefaultTimeout(30_000)
const assetTasks = []; report.client_assets = []
page.on('response', response => {
  const target = new URL(response.url())
  if (target.origin === new URL(env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/').origin
    && /\.(m?js|css)$/.test(target.pathname) && response.ok()) {
    assetTasks.push(response.body().then(bytes => report.client_assets.push({path: target.pathname,
      digest: hash(bytes), size_bytes: bytes.length})).catch(error => report.errors.push(`Client asset not retained: ${error.message}`)))
  }
})
page.on('pageerror', error => report.errors.push(error.message))
const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
const button = (widget, id) => host(widget).locator(`[data-command-id=${JSON.stringify(id)}]`)
const compare = () => host('retouch_comparison')
const field = (widget, id) => host(widget).locator(`[data-webui-field-id=${JSON.stringify(id)}]`).locator('input,textarea,select').first()
const galleryRow = id => host('v_photos').locator(`[data-focus-ref=${JSON.stringify(id)}], [data-focus-ref$=${JSON.stringify(':'+id)}]`)
const galleryItems = () => host('v_photos').locator('.collection-focus-item[data-focus-ref]')
async function responseFor(action, tool, command, { allowFailure = false } = {}) {
  const response = page.waitForResponse(r => {
    if (new URL(r.url()).pathname !== '/api/tools/call') return false
    const b = r.request().postDataJSON()
    return b?.tool === `natural_retouch_skill:${tool}` && (!command || b.arguments?.command === command)
  }, { timeout: 60_000 })
  await action()
  const http = await response, body = await http.json(), value = body.result ?? body
  if (!allowFailure) expect(http.ok() && body.ok !== false && value.ok !== false).toBe(true)
  expect(http.headers()['x-adaos-runtime-source']).toBe('fixture')
  return value
}
async function step(id, action) {
  report.active = id
  const start = Date.now(), evidence = await action()
  const facts = await audit()
  report.checks.push({ id, passed: true, duration_ms: Date.now() - start, evidence,
    state_digest: hash(facts), jobs: facts.jobs.map(j => ({ request_id: j.request_id, submits: j.submits, polls: j.polls })) })
  await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2))
  console.log(`${layout}: ${id} passed`)
}
const sample = Buffer.from(await (await fixture('/sample-image')).arrayBuffer())
let photo, run, second
const control = values => fixture('/control', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)})
async function canvas() {
  if (layout !== 'compact') return
  const close=page.locator('ada-layout-region.is-open .layout-region__header button').first()
  if (await close.isVisible()) await close.click()
}
async function reveal(id) {
  if (await host(id).isVisible()) return
  const trigger=page.locator('.layout-region-trigger[aria-expanded="false"]').first()
  if (await trigger.count()) await trigger.click()
  await expect(host(id)).toBeVisible()
  await host(id).scrollIntoViewIfNeeded()
}
async function ready() {
  await page.waitForFunction(({scenario,expected})=>{
    const sync=window.__ADAOS_DEBUG_STATE__?.()?.sync,v=sync?.materialization||{}
    return sync?.materializationReady && v.currentScenario===scenario &&
      Object.entries(expected).every(([k,val])=>({revision:v.materializationRevision,source_fingerprint:v.sourceFingerprint})[k]===val)
  },{scenario,expected:expectedMaterialization},{timeout:90000})
  const observed=await page.evaluate(()=>{const v=window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization||{};return {revision:v.materializationRevision,source_fingerprint:v.sourceFingerprint,scenario:v.currentScenario}})
  report.observed_materializations||=[];report.observed_materializations.push(observed)
  await expect(host('retouch_comparison')).toBeAttached()
  await reveal('choose_photo')
}
async function gallery() {
  if (await host('v_photos').isVisible()) return
  await reveal('choose_photo')
  await host('choose_photo').locator('button[data-command-id]').first().click()
  await expect(host('v_photos')).toBeVisible()
}
async function closeModal() {
  const modal=page.locator('ion-modal').last()
  if(await modal.isVisible()) await modal.getByRole('button',{name:/^(Close|Закрыть)$/i}).click()
  // Ionic dismisses asynchronously. A following gallery() must not reuse the
  // outgoing list while its modal is still visible and about to be detached.
  await expect(modal).not.toBeVisible()
}
async function selectPhoto(item) {
  await gallery()
  await galleryRow(item.id).click()
  await expect(host('v_photos')).not.toBeVisible()
  await canvas()
  await expect(compare().locator('.comparison-image').first()).toBeVisible()
  await expect.poll(()=>compare().locator('.comparison-image').first().evaluate(n=>n.complete&&n.naturalWidth)).toBe(320)
}
async function upload(name) {
  await gallery();await button('v_photos','cmd_add_photo').click()
  await expect(host('e_add_photo')).toBeVisible()
  await field('e_add_photo','filename').fill(name)
  await field('e_add_photo','format').fill('JPEG')
  await field('e_add_photo','date').fill('2026-10-19')
  const uploaded=observePending(page.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname.endsWith('/upload_attachment/attachments')))
  await host('e_add_photo').locator('input[type=file]').setInputFiles({name,mimeType:'image/jpeg',buffer:sample})
  expect((await uploaded).ok()).toBe(true)
  const value=await responseFor(()=>button('e_add_photo','cmd_add_photo').click(),'mutate_record')
  await expect(host('e_add_photo')).not.toBeVisible()
  return value.item
}
async function tool(id) {await reveal('retouch_mask_controls');await button('retouch_mask_controls',id).click();await canvas()}
async function masks() {
  await canvas();await compare().getByRole('button',{name:/^(Оригинал|Original)$/}).click()
  await compare().getByRole('button',{name:'100%',exact:true}).click()
  await tool('rectangle');await tool('crop')
  const stage=compare().locator('.stage').first();await stage.scrollIntoViewIfNeeded()
  let b=await stage.boundingBox(),left=b.x+b.width/2-160,top=b.y+b.height/2-120
  await stage.hover({position:{x:b.width/2-159.5,y:b.height/2-119.5}})
  b=await stage.boundingBox();left=b.x+b.width/2-160;top=b.y+b.height/2-120
  await page.mouse.down();await page.mouse.move(left+319.5,top+239.5,{steps:12});await page.mouse.up()
  await reveal('retouch_radius');await field('retouch_radius','radius').fill('8');await field('retouch_radius','radius').press('Tab')
  await tool('protect');b=await stage.boundingBox()
  // Locator actionability waits for the compact control sheet to finish
  // closing; a raw coordinate click can hit its outgoing overlay instead.
  await stage.click({position:{x:b.width/2-110,y:b.height/2-70}})
  await expect(compare().locator('.mask-draft-status[role="status"]')).toBeVisible()
}
async function params(goal='Убрать тестовый прыщ; сохранить поры, родинки и геометрию.',strength='30') {
  await reveal('retouch_task')
  await field('retouch_task','target_area').fill(goal)
  await field('retouch_task','strength').fill(strength)
  await field('retouch_task','max_run_usd').fill('1')
}
async function start({allowFailure=false}={}) {
  await reveal('retouch_task')
  const submit=button('retouch_task','retouch')
  await expect(submit,'An explicit start must be available after completion, cancellation or a known terminal failure').toBeVisible()
  await expect(submit).not.toHaveAttribute('aria-disabled','true')
  const value=await responseFor(()=>submit.click(),'run_command','retouch',{allowFailure})
  return value
}
async function completed(id) {
  await expect.poll(async()=> (await audit()).runs.find(r=>r.id===id)?.status,{timeout:180000,intervals:[500,1000]}).toBe('completed')
  return (await audit()).runs.find(r=>r.id===id)
}
try {
  await page.goto(clientUrl.href,{waitUntil:'domcontentloaded'});await ready()
  await step('loading-error-empty',async()=>{
    if (layout==='wide') {
      const image=await compare().boundingBox(), controls=await host('choose_photo').boundingBox()
      expect(controls.x, 'Photo picker and controls must sit to the right of the canvas on a wide screen').toBeGreaterThanOrEqual(image.x+image.width-1)
      expect(controls.y, 'Photo picker must be visible at the top, without scrolling past history').toBeLessThan(220)
      expect(controls.width).toBeLessThan(image.width)
    }
    await expect(host('v_results')).not.toBeVisible()
    await expect(host('v_downloads')).not.toBeVisible()
    await gallery();await expect.poll(()=>firstPhotoRead).toBe(true)
    await expect(host('v_photos')).toHaveAttribute('data-webui-data-state','loading')
    expect(await host('v_photos').innerText()).not.toMatch(/нет фотографий|no photos/i)
    initialReadRelease();await expect(host('v_photos')).toHaveAttribute('data-webui-data-state',/error|unavailable/)
    photoReadFailure=false
    await host('v_photos').getByRole('button',{name:/^(Retry|Повторить|Повторить попытку)$/i}).click()
    await expect(host('v_photos')).toHaveAttribute('data-webui-data-state','ready')
    await expect(galleryItems()).toHaveCount(0)
  })
  await step('upload-jpeg-lossless',async()=>{
    photo=await upload(`fixture-${layout}.jpg`);await selectPhoto(photo)
    expect(photo.format).toBe('JPEG');expect([photo.width,photo.height]).toEqual([320,240])
    return {photo_id:photo.id,input_digest:hash(sample)}
  })
  await step('bounded-thumbnail-picker',async()=>{
    await gallery();const card=galleryRow(photo.id)
    await expect(card).toContainText('320');await expect(card).toContainText('240');await expect(card).toContainText('JPEG')
    await expect(card).toContainText(/2026/)
    const img=card.locator('img');await expect(img).toHaveCount(1)
    await expect.poll(()=>img.evaluate(n=>n.complete&&n.naturalWidth>0&&Math.max(n.naturalWidth,n.naturalHeight)<=384)).toBe(true)
    expect(await img.getAttribute('src')).not.toContain('/originals/')
    const search=host('queries-v_photos').locator('input[type=search]')
    await search.fill('not-a-fixture-photo');await expect(galleryItems()).toHaveCount(0)
    await search.fill('');await expect(card).toBeVisible();await closeModal()
  })
  await step('local-draft-preservation',async()=>{
    await selectPhoto(photo);await masks()
    second=await upload(`second-${layout}.jpg`);await selectPhoto(second);await selectPhoto(photo)
    await expect(compare().locator('.mask-draft-status[role="status"]')).toBeVisible()
    expect((await audit()).jobs).toHaveLength(0)
  })
  await step('goal-parameters-no-submit',async()=>{
    await params();await gallery();await closeModal();await reveal('retouch_task')
    await expect(field('retouch_task','target_area')).toHaveValue('Убрать тестовый прыщ; сохранить поры, родинки и геометрию.')
    expect((await audit()).jobs).toHaveLength(0)
  })
  await step('rectangle-protection',async()=>{
    await canvas();await expect(compare().locator('.mask-draft-status[role="status"]')).toBeVisible()
    const toolbar=compare().getByRole('toolbar',{name:/^(Masks|Маски)$/});await expect(toolbar).toHaveCount(0)
    expect((await audit()).jobs).toHaveLength(0)
  })
  await step('budget-admission-failure-visible',async()=>{
    await control({exhaust_budget:true});const value=await start({allowFailure:true})
    expect(value.ok).toBe(false)
    expect(String(value.human_message||value.message||value.error)).toMatch(/бюджет|лимит|budget|limit/i)
    const refused=await audit()
    expect(refused.jobs).toHaveLength(0);expect(refused.runs).toHaveLength(0);expect(refused.continuations).toHaveLength(0)
    await expect(field('retouch_task','target_area')).toHaveValue('Убрать тестовый прыщ; сохранить поры, родинки и геометрию.')
    await control({exhaust_budget:false})
    if(variant==='edited') await params(undefined,'31')
  })
  await step('atomic-start-goal-scope',async()=>{
    await control({hold_phase:'edit'});const value=await start();run=value.item
    expect(run?.id).toBeTruthy()
    const facts=await audit();expect(facts.jobs).toHaveLength(1);expect(facts.jobs[0].kind).toBe('edit')
    expect(facts.continuations).toHaveLength(1);expect(facts.continuations[0].photo_id).toBe(photo.id)
    expect(facts.jobs[0].submits).toBe(1)
    const saved=facts.runs.find(r=>r.id===run.id)
    expect(saved.mask.permission_mode).toBe('rectangle')
    expect(saved.mask.crop).toEqual({x:0,y:0,width:320,height:240})
    expect(saved.mask.strokes).toHaveLength(1)
    const protection=saved.mask.strokes[0]
    expect(protection.kind).toBe('protect');expect(protection.radius).toBe(8)
    expect(protection.points).toHaveLength(1)
    // Pointer coordinates are fractional CSS/source pixels; browser layout
    // quantization must not turn a correct 49.99px click into a failure.
    expect(protection.points[0].x).toBeCloseTo(50,1)
    expect(protection.points[0].y).toBeCloseTo(50,1)
    expect(facts.jobs[0].edit_prompt).toContain('Убрать тестовый прыщ')
    await expect(button('retouch_task','retouch'),'A running job must disable duplicate Start').toHaveAttribute('aria-disabled','true')
    await canvas();await expect(compare().locator('.mask-draft-status[role="status"]')).not.toBeVisible()
    return {run_id:run.id,mask:saved.mask,prompt:facts.jobs[0].edit_prompt}
  })
  await step('automatic-progress-reopen-idempotence',async()=>{
    await expect.poll(async()=> (await audit()).jobs[0].polls,{timeout:15000}).toBeGreaterThan(0)
    await expect(host('retouch_run_state')).toContainText(/ретуш|retouch|ожидан|выполн/i)
    await page.goto('about:blank')
    const saved=await audit()
    if(env.ADAOS_E2E_RESTART_FIXTURE==='1') {
      await expect.poll(async()=> (await audit()).continuations.find(r=>r.photo_id===photo.id)?.status,{timeout:15000}).toBe('waiting')
      restartWindow=true
      const restartMarker=path.join(fixtureRoot,`restart-requested-${crypto.randomUUID()}.tmp`)
      await fs.writeFile(restartMarker,JSON.stringify({run_id:run.id}))
      await fs.rename(restartMarker,path.join(fixtureRoot,'restart-requested.json'))
      await expect.poll(async()=>{try{await fs.access(path.join(fixtureRoot,'restart-complete.json'));return true}catch{return false}},{timeout:60000}).toBe(true)
      restartWindow=false;report.fixture_restart={run_id:run.id,same_owned_store:true}
    }
    await control({hold_phase:null});run=await completed(run.id)
    const final=await audit();expect(final.jobs.map(j=>j.kind)).toEqual(['edit']);expect(final.jobs.every(j=>j.submits===1)).toBe(true)
    expect(final.jobs[0].request_id).toBe(saved.jobs[0].request_id)
    await page.goto(clientUrl.href,{waitUntil:'domcontentloaded'});await ready();await selectPhoto(photo);await canvas()
    await expect(compare().locator('.comparison-image')).toHaveCount(2)
    return {run_id:run.id,jobs:final.jobs,completed_without_page:true}
  })
  await step('completed-run-cost-authority',async()=>{
    const facts=await audit(),attempts=facts.budget.attempts.filter(r=>r.run_id===run.id)
    expect(attempts).toHaveLength(1);expect(attempts.every(r=>r.status==='reconciled'&&r.cost_basis==='metered')).toBe(true)
    const expected=attempts.reduce((sum,row)=>sum+row.observed_usd,0)
    await reveal('retouch_run_state')
    await expect.poll(async()=>{
      const text=await host('retouch_run_state').innerText()
      const match=text.match(/(?:confirmed spend|подтвержд[её]нный расход):\s*(\d+(?:[.,]\d+)?)\s*USD/iu)
      return match?Number(match[1].replace(',','.')):null
    }).toBe(expected)
  })
    await step('compare-zoom-pan-difference-download', async () => {
    await reveal('v_results')
    await host('v_results').locator(`[data-record-id="${run.id}-view-raw"]`).click(); await canvas()
    await expect(compare()).toContainText(/маски не применены/i)
    const rawDownload=observePending(page.waitForEvent('download'))
    await compare().getByRole('button',{name:/^(Скачать результат|Download result)$/}).click()
    const rawFile=await rawDownload,rawName=rawFile.suggestedFilename(),rawPath=path.join(output,rawName)
    await rawFile.saveAs(rawPath)
    report.raw_preview={filename:rawName,digest:hash(await fs.readFile(rawPath)),run_id:run.id}
    const beforeMasks=await audit()
    await reveal('retouch_result_commands')
    await responseFor(()=>page.getByRole('button',{name:'Применить маски',exact:true}).click(),'prepare_comparison')
    await canvas();await expect(compare()).toContainText(/по маскам/i)
    const afterMasks=await audit()
    expect(afterMasks.jobs).toEqual(beforeMasks.jobs)
    expect(afterMasks.budget.committed_usd).toBe(beforeMasks.budget.committed_usd)
    await host('v_results').locator(`[data-record-id="${run.id}-view-masked"]`).click();await canvas()
    await expect(compare()).toBeVisible()
    await compare().getByRole('button', { name: /^(Сравнить|Compare)$/ }).click()
    const images = compare().locator('.comparison-image'); await expect(images).toHaveCount(2)
    await expect.poll(() => images.evaluateAll(nodes => nodes.every(n => n.complete && n.naturalWidth === 320))).toBe(true)
    await compare().getByRole('button', { name: '100%', exact: true }).click()
    await expect(compare().locator('output.zoom')).toHaveText('100%')
    const worlds = () => compare().locator('.world').evaluateAll(nodes => nodes.map(n => n.style.transform))
    const beforePan = await worlds(); expect(beforePan[0]).toBe(beforePan[1])
    await compare().locator('.stage').first().press('ArrowLeft')
    const afterPan = await worlds(); expect(afterPan[0]).toBe(afterPan[1]); expect(afterPan).not.toEqual(beforePan)
    await compare().getByRole('button', { name: /^(Показать изменения|Show changes)$/ }).click()
    await expect(compare().locator('.difference-map')).toHaveCount(2)
    await expect(compare().locator('.difference-info')).toContainText(/без изменений|unchanged/i)
    const differenceBytes = await compare().locator('.difference-map').first().evaluate(async node =>
      [...new Uint8Array(await (await fetch(node.src)).arrayBuffer())])
    await fs.writeFile(path.join(output, 'difference-map.png'), Buffer.from(differenceBytes))
    const download = observePending(page.waitForEvent('download'))
    await compare().getByRole('button', { name: /^(Скачать результат|Download result)$/ }).click()
    const file = await download, name = file.suggestedFilename(), saved = path.join(output, name)
    await file.saveAs(saved); const bytes = await fs.readFile(saved)
    expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a')
    expect([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]).toEqual([320, 240])
    report.downloads.push({ filename: name, digest: hash(bytes), size: bytes.length })
    const maskedJpeg=host('v_results').locator('[data-record-id]').filter({hasText:run.id.slice(0,12)}).filter({hasText:'JPEG'}).filter({hasText:/по маскам/i})
    await expect(maskedJpeg,'A prepared masked variant must expose exactly one JPEG history row').toHaveCount(1)
    await maskedJpeg.click(); await canvas()
    const jpegDownload = observePending(page.waitForEvent('download'))
    await compare().getByRole('button', { name: /^(Скачать результат|Download result)$/ }).click()
    const jpeg = await jpegDownload, jpegName = jpeg.suggestedFilename(), jpegPath = path.join(output, jpegName)
    await jpeg.saveAs(jpegPath); const jpegBytes = await fs.readFile(jpegPath)
    expect(jpegBytes.subarray(0, 2).toString('hex')).toBe('ffd8')
    const geometry = await page.evaluate(async data => {
      const bitmap = await createImageBitmap(new Blob([new Uint8Array(data)], { type: 'image/jpeg' }))
      const size = [bitmap.width, bitmap.height]; bitmap.close(); return size
    }, [...jpegBytes])
    expect(geometry).toEqual([320, 240])
    report.downloads.push({ filename: jpegName, digest: hash(jpegBytes), size: jpegBytes.length })
    if(variant==='edited') {
      const beforeSwitch=await audit()
      await page.getByRole('button',{name:'Ответ ИИ — без масок',exact:true}).click()
      await expect(compare(),'Raw inspection must also work when a JPEG history row is selected; an absent raw jpeg_download must fall back to the raw PNG').toContainText(/маски не применены/i)
      expect((await audit()).jobs).toEqual(beforeSwitch.jobs)
      await page.getByRole('button',{name:'По маскам',exact:true}).click()
      await expect(compare()).toContainText(/по маскам/i)
    }
    return { before_pan: beforePan, after_pan: afterPan, downloaded: [name, jpegName] }
  })

  await step('adopt-incremented-original',async()=>{
    const before=await audit();await reveal('retouch_result_commands')
    await expect(button('retouch_result_commands','adopt_result'),
      'Visible Russian text must survive source generation. Literal ??? indicates PowerShell ASCII-pipe corruption; repair all affected labels/messages with UTF-8-safe writes.').toHaveText(/сохранить как новый исходник|save as new original/i)
    await expect(button('retouch_result_commands','adopt_result'),
      'Adoption of the displayed prepared masked artifact must be enabled; use only supported AdaOS state-expression operands').toBeEnabled()
    await responseFor(()=>button('retouch_result_commands','adopt_result').click(),'adopt_result')
    await gallery();await expect(host('v_photos')).toContainText(/_001\.png/)
    expect((await audit()).jobs).toEqual(before.jobs);await closeModal()
  })
  await step('completed-mask-new-run',async()=>{
    await selectPhoto(photo);const before=await audit(),old=before.runs.find(r=>r.id===run.id)
    await compare().getByRole('button',{name:/^(Оригинал|Original)$/}).click()
    await compare().getByRole('button',{name:'100%',exact:true}).click();await tool('protect')
    const stage=compare().locator('.stage').first();await stage.scrollIntoViewIfNeeded();const b=await stage.boundingBox()
    await stage.click({position:{x:b.width/2+50,y:b.height/2+30}})
    await params('Убрать только второе тестовое пятно; сохранить ткань.','37')
    const value=await start();expect(value.item.id).not.toBe(run.id)
    const newer=await completed(value.item.id),after=await audit()
    await reveal('v_results')
    await expect(host('v_results').locator(`[data-record-id="${newer.id}-view-raw"]`),
      'The new result must appear and be selected automatically while the page stays open').toHaveAttribute('aria-selected','true')
    await canvas();await expect(compare().locator('.comparison-image')).toHaveCount(2)
    expect(after.runs.find(r=>r.id===run.id)).toEqual(old)
    expect(after.jobs).toHaveLength(before.jobs.length+1);expect(after.jobs.every(j=>j.submits===1)).toBe(true)
    report.revision_probe={original_run_id:run.id,new_run_id:newer.id,old_run_unchanged:true,no_duplicate_submits:true}
  })
  await step('result-input-history',async()=>{
    await reveal('v_results');await host('v_results').locator(`[data-record-id="${run.id}-view-raw"]`).click()
    await reveal('d_result_inputs')
    const provenance=page.locator('[data-webui-label="Параметры выбранного результата"],[data-webui-label="Selected result inputs"]').filter({visible:true}).last()
    await expect(provenance).toContainText('Убрать тестовый прыщ')
    await expect(provenance).not.toContainText('второе тестовое пятно')
    await expect(host('v_results')).toContainText(/2026/)
    expect(await host('v_results').innerText()).not.toContain('/api/tools/')
    await canvas();const pending=observePending(page.waitForEvent('download'))
    await compare().getByRole('button',{name:/^(Скачать результат|Download result)$/}).click()
    const file=await pending,saved=path.join(output,'history-original.png');await file.saveAs(saved)
    expect(hash(await fs.readFile(saved))).toBe(report.raw_preview.digest)
  })
  await step('cancel-and-terminal-recovery',async()=>{
    await selectPhoto(second);await masks();await params('Убрать только тестовый прыщ; сохранить всё остальное.','32')
    await control({unknown_submit_phase:'edit',hold_phase:'edit'})
    const uncertain=(await start()).item
    expect(uncertain.id).toBeTruthy()
    const held=await audit(),before=held.budget.committed_usd
    await reveal('retouch_run_state')
    const cancel=page.locator('ada-page-widget-host [data-command-id="cancel"]:visible').first()
    await expect(cancel,'Cancel must be accessible beside live progress').toBeVisible()
    await responseFor(()=>cancel.click(),'run_command','cancel')
    await expect.poll(async()=> (await audit()).runs.find(r=>r.id===uncertain.id)?.status).toBe('cancelled')
    await page.waitForTimeout(4000)
    const cancelled=await audit()
    expect(cancelled.budget.committed_usd).toBe(before)
    expect(cancelled.jobs).toHaveLength(held.jobs.length)
    expect(cancelled.jobs.every(j=>j.submits===1)).toBe(true)
    await control({unknown_submit_phase:null,hold_phase:null,fail_phase:'edit'})
    await params('Убрать тестовое пятно для проверки сбоя; сохранить фактуру.','33')
    const failed=(await start()).item
    expect(failed.id).not.toBe(uncertain.id)
    await expect.poll(async()=> (await audit()).runs.find(r=>r.id===failed.id)?.status,{timeout:30000}).toBe('edit_failed')
    const prior=(await audit()).runs.find(r=>r.id===failed.id)
    await control({fail_phase:null});await params('Убрать тестовое пятно после сбоя; сохранить фактуру.','34')
    const retry=(await start()).item
    expect(retry.id,'A known terminal failure must not block a new explicitly changed task').not.toBe(failed.id)
    await completed(retry.id)
    const final=await audit()
    expect(final.runs.find(r=>r.id===failed.id)).toEqual(prior)
    expect(final.jobs.every(j=>j.submits===1)).toBe(true)
    return {cancelled_run:uncertain.id,failed_run:failed.id,recovered_run:retry.id,unknown_reservation_retained:true}
  })
  report.passed = report.checks.length === specification.obligations.length && !report.errors.length
  report.incomplete_obligations = specification.obligations.filter(id => !report.checks.some(check => check.id === id))
  report.passed = report.passed && !report.incomplete_obligations.length
} catch (error) {
  report.failure = String(error.message).replaceAll(fixtureToken, '[redacted]').replaceAll(token, '[redacted]')
  const causeCode = String(error.cause?.code || error.code || '')
  report.failure_cause_code = /^[A-Z0-9_]{1,80}$/.test(causeCode) ? causeCode : null
  report.failure_kind = /expect\(/.test(report.failure) ? 'candidate_postcondition' : 'runner_or_environment'
  console.log(`${layout}: ${report.active} FAILED: ${report.failure.slice(0, 700)}`)
}
finally {
  initialReadRelease()
  report.text = await page.locator('body').innerText().catch(() => '')
  report.final_audit = await audit()
  await Promise.allSettled(assetTasks)
  report.client_assets.sort((a, b) => a.path.localeCompare(b.path))
  report.passed = report.passed && !report.errors.length && !report.failure && report.client_assets.length > 0 && !report.supervisor_interventions?.length
  report.runtime = await page.evaluate(() => window.__ADAOS_DEBUG_STATE__?.()).catch(() => null)
  await page.screenshot({ path: path.join(output, 'final.png'), fullPage: true }).catch(() => {})
  await context.tracing.stop({ path: path.join(output, 'private-trace.zip') })
  await fs.writeFile(path.join(output, 'journey.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, failure: report.failure,
  incomplete: report.incomplete_obligations, external_model_calls: report.final_audit.external_model_calls }))
if (!report.passed) process.exitCode = 1
