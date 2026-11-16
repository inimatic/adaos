import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { once } from 'node:events'

const root = fileURLToPath(new URL('../../../', import.meta.url))
const client = path.join(root, 'src/adaos/integrations/adaos-client')
const requireClient = createRequire(path.join(client, 'package.json'))
const requireBrowser = createRequire(new URL('./package.json', import.meta.url))
const { chromium } = requireBrowser('@playwright/test')
const esbuild = requireClient('esbuild')
const task = path.resolve(process.env.ADAOS_CHANNEL_TASK || path.join(root, '.tmp/channel-reliability'))
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || path.join(root, 'e2e/artifacts/channel-reliability'))
await mkdir(output, { recursive: true })
await mkdir(path.join(task, 'runtime'), { recursive: true })
const python = process.env.ADAOS_E2E_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const core = spawn(python, [path.join(root, 'e2e/stand/fixtures/channel-reliability-core.py'), '--runtime-dir', path.join(task, 'runtime'), '--initial-delay-ms', process.env.ADAOS_CHANNEL_INITIAL_DELAY_MS || '0'], { cwd: root, env: process.env, windowsHide: true })
let coreLog = ''
core.stdout.on('data', data => { coreLog += data })
core.stderr.on('data', data => { coreLog += data })
const browserErrors = []
let browser, server
const checks = []
const samples = []
const started = Date.now()
async function until(read, predicate, label, timeout = 15000) {
  const deadline = Date.now() + timeout
  let value
  while (Date.now() < deadline) {
    value = await read()
    if (predicate(value)) return value
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  throw new Error(`${label}: timeout, last=${JSON.stringify(value)}`)
}
try {
  const ready = await until(async () => {
    if (core.exitCode !== null) throw new Error(coreLog)
    return coreLog.match(/\{"ready": true, "port": (\d+)\}/)
  }, Boolean, 'fixture startup', 30000)
  const fixture = `http://127.0.0.1:${ready[1]}`
  const ws = fixture.replace('http:', 'ws:')
  const code = `
    import * as Y from 'yjs';
    import { WebsocketProvider } from 'y-websocket';
    import { DataChannelProvider } from './src/app/y/datachannel-provider';
    window.doc = new Y.Doc();
    window.events = [];
    window.diagnostics = [];
    window.rtc = async () => {
      const pc = window.pc = new RTCPeerConnection({iceServers: []});
      const dc = window.dc = pc.createDataChannel('yjs', {ordered: true});
      const p = window.provider = new DataChannelProvider(window.doc, dc);
      p.on('sync', value => window.events.push({transport:'rtc', synced:value,
        boot:window.doc.getMap('data').get('boot'),large:window.doc.getMap('data').get('large')?.length || 0}));
      p.on('diagnostic', value => window.diagnostics.push(value));
      await pc.setLocalDescription(await pc.createOffer());
      if (pc.iceGatheringState !== 'complete') await new Promise(resolve => pc.addEventListener('icegatheringstatechange', () => { if (pc.iceGatheringState === 'complete') resolve(); }));
      const response = await fetch('/api/offer', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(pc.localDescription)});
      if (!response.ok) throw new Error('offer: ' + await response.text());
      await pc.setRemoteDescription(await response.json());
    };
    window.yws = () => {
      window.provider?.destroy();
      window.pc?.close();
      const p = window.provider = new WebsocketProvider(${JSON.stringify(ws + '/yws')}, 'fixture', window.doc, {disableBc:true});
      p.on('sync', value => window.events.push({transport:'yws', synced:value}));
    };
    window.vector = () => Object.fromEntries(Y.decodeStateVector(Y.encodeStateVector(window.doc)));
    window.resync = () => {
      if (window.provider instanceof DataChannelProvider) window.provider.sendSyncStep1();
      else {
        // y-websocket's public resync interval uses this exact STEP1 frame.
        const vector = Y.encodeStateVector(window.doc);
        const header = [0,0]; let n=vector.length;
        while (n>127) {header.push((n&127)|128);n>>>=7;} header.push(n);
        window.provider.ws.send(new Uint8Array([...header,...vector]));
      }
    };
    window.setValue = (key,value) => window.doc.getMap('data').set(key,value);
  `
  const bundle = await esbuild.build({ stdin: { contents: code, loader: 'ts', resolveDir: client }, bundle: true, format: 'esm', write: false })
  server = createServer(async (request, response) => {
    try {
      if (request.url.startsWith('/api/')) {
        const chunks = []
        for await (const data of request) chunks.push(data)
        const upstream = await fetch(fixture + request.url.slice(4), {method:request.method, headers:{'Content-Type':'application/json'}, ...(chunks.length ? {body:Buffer.concat(chunks)} : {})})
        response.writeHead(upstream.status, {'Content-Type':'application/json'})
        response.end(await upstream.text())
      } else if (request.url === '/app.js') {
        response.writeHead(200, {'Content-Type':'text/javascript'})
        response.end(bundle.outputFiles[0].text)
      } else {
        response.writeHead(200, {'Content-Type':'text/html'})
        response.end('<!doctype html><script type="module" src="/app.js"></script>')
      }
    } catch (error) { response.writeHead(500); response.end(String(error)) }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  browser = await chromium.launch({ executablePath: process.env.ADAOS_E2E_BROWSER_EXECUTABLE || (process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined), headless: true })
  const page = await browser.newPage()
  page.on('pageerror', error => browserErrors.push(error.stack))
  await page.goto(`http://127.0.0.1:${server.address().port}`)
  await page.waitForFunction(() => typeof window.rtc === 'function')
  const state = async () => (await fetch(fixture + '/state')).json()
  const post = async (endpoint, body) => {
    const response = await fetch(fixture + endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
    assert.equal(response.status, 200, await response.text())
  }
  async function converged(label) {
    const server = await state()
    await page.waitForFunction(vector => JSON.stringify(Object.entries(window.vector()).sort()) === JSON.stringify(Object.entries(vector).sort()), server.vector)
    assert.deepEqual(await page.evaluate(() => window.doc.getMap('data').toJSON()), {
      ...server.data, large: 'x'.repeat(server.data.large),
    })
    samples.push({label, vector:server.vector, modes:server.modes.length, clients:server.clients})
  }
  await page.evaluate(() => window.rtc())
  await page.waitForFunction(() => window.provider.synced && window.dc.readyState === 'open')
  assert.equal(await page.evaluate(() => window.events.filter(event => event.synced).every(event => event.boot==='authoritative' && event.large===512*1024)),true,'sync readiness requires the initial snapshot, even if live updates arrive first')
  await converged('initial RTC with GC fixture and 512 KiB snapshot')
  checks.push('real Chrome/aiortc SCTP channel: chunked first sync and equal JS/native clocks')

  for (let n=0;n<5;n++) {
    await post('/mutate', {key:'server',value:n})
    await page.waitForFunction(n => window.doc.getMap('data').get('server') === n, n)
    await page.evaluate(n => window.setValue('browser',n), n)
    await until(state, value => value.data.browser === n, 'browser update reaches native room')
    await page.evaluate(() => window.resync())
  }
  await converged('RTC mutations and five resyncs')
  checks.push('five bidirectional edit/resync cycles without generation guard')

  await post('/fault', {kind:'rtc'})
  await page.waitForFunction(() => !window.provider.synced)
  await post('/mutate', {key:'during_rtc_outage',value:17})
  await page.evaluate(() => window.yws())
  await page.waitForFunction(() => window.provider.synced)
  await page.waitForFunction(() => window.doc.getMap('data').get('during_rtc_outage') === 17)
  await converged('YWS after RTC outage')
  checks.push('physical RTC closure is reported disconnected; YWS obtains missed server state')

  for (let n=0;n<3;n++) {
    const before = (await state()).counts.yws
    await post('/fault', {kind:'yws'})
    await post('/mutate', {key:'during_yws_outage',value:n})
    await until(state, value => value.counts.yws > before, 'YWS reconnect')
    await page.waitForFunction(n => window.provider.synced && window.doc.getMap('data').get('during_yws_outage') === n, n)
    await page.evaluate(() => window.resync())
  }
  await converged('three YWS reconnects')
  checks.push('three YWS socket failures reconnect and recover missed server updates')

  await post('/fault', {kind:'restart'})
  await page.waitForFunction(() => window.provider.synced)
  await post('/mutate', {key:'after_restart',value:true})
  await page.waitForFunction(() => window.doc.getMap('data').get('after_restart') === true)
  await converged('persisted native room restart')
  checks.push('native snapshot restart retains data and clocks')

  await page.evaluate(() => window.provider.destroy())
  await page.evaluate(() => window.rtc())
  await page.waitForFunction(() => window.provider.synced && window.dc.readyState === 'open')
  await post('/mutate', {key:'back_on_rtc',value:true})
  await page.waitForFunction(() => window.doc.getMap('data').get('back_on_rtc') === true)
  const beforeResync = (await state()).modes.length
  for (let n=0;n<10;n++) await page.evaluate(() => window.resync())
  await until(state, value => value.modes.length >= beforeResync + 10, 'ten RTC resync responses')
  await converged('return to RTC and ten resyncs')
  checks.push('return to RTC converges after YWS/restart without a reload')
  await page.waitForFunction(() => window.diagnostics.some(event => event.reason==='webrtc_yjs_probe_ack'), null, {timeout:25000})
  const idleProof = await page.evaluate(() => window.provider.getLivenessSnapshot())
  assert.equal(idleProof.fresh,true)
  assert.ok(idleProof.roundTripMs !== null)
  checks.push('idle RTC renews its proof through an actual native STEP1/STEP2 round trip')
  const stallStarted = Date.now()
  await post('/fault', {kind:'stall_rtc'})
  assert.equal(await page.evaluate(() => window.dc.readyState), 'open')
  await page.waitForFunction(() => !window.provider.synced, null, {timeout:35000})
  const stallDetectionMs = Date.now()-stallStarted
  assert.ok(stallDetectionMs < 30000)
  assert.equal(await page.evaluate(() => window.diagnostics.some(event => event.reason==='webrtc_yjs_liveness_timeout')), true)
  checks.push('silent Yjs request blackhole with open ICE/SCTP fails within 30 seconds')
  await page.evaluate(() => window.yws())
  await page.waitForFunction(() => window.provider.synced)
  await post('/mutate', {key:'after_blackhole',value:true})
  await page.waitForFunction(() => window.doc.getMap('data').get('after_blackhole')===true)
  await converged('YWS after silent RTC request blackhole')
  const final = await state()
  await writeFile(path.join(output, 'final-state.json'), JSON.stringify(final,null,2)+'\n')
  assert.equal(final.modes.length >= 20, true)
  assert.equal(final.modes.every(mode => mode === 'diff'), true, JSON.stringify(final.modes))
  assert.deepEqual(final.errors, [])
  assert.deepEqual(browserErrors, [])
  assert.equal(final.clients, 1)
  assert.equal(final.counts.rtc, 2)
  assert.equal(final.counts.yws, 6)
  const result = {result:'passed', seconds:(Date.now()-started)/1000, idleProof, stallDetectionMs, initialDelayMs:Number(process.env.ADAOS_CHANNEL_INITIAL_DELAY_MS || 0), checks, samples, final,
    scope:'Loopback providers, production native/room/admission/adapter. Transport selection is driven by the harness; public Root, ICE across NAT and UI coordinator are separate qualifications.'}
  await writeFile(path.join(output, 'result.json'), JSON.stringify(result,null,2)+'\n')
  console.log(JSON.stringify({result:result.result, seconds:result.seconds, checks, counts:final.counts}))
} catch (error) {
  await writeFile(path.join(output,'failure.json'), JSON.stringify({error:String(error.stack), checks, samples, browserErrors},null,2)+'\n')
  throw error
} finally {
  await browser?.close()
  server?.close()
  core.kill()
  if (core.exitCode === null) await once(core,'exit')
  await writeFile(path.join(output,'core.log'),coreLog)
}
