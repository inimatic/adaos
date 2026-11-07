import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../../', import.meta.url))
const client = `${root}/src/adaos/integrations/adaos-client`
const requireClient = createRequire(`${client}/package.json`)
const requireBrowser = createRequire(`${root}/e2e/stand/browser/package.json`)
const { chromium } = requireBrowser('@playwright/test')
const { WebSocketServer } = requireClient('ws')
const esbuild = requireClient('esbuild')
const Y = requireClient('yjs')
const encoding = requireClient('lib0/encoding')
const decoding = requireClient('lib0/decoding')
const sync = requireClient('y-protocols/sync')
const adapter = (await readFile(`${client}/src/app/core/adaos/shared-node-connection.service.ts`, 'utf8'))
  .replace("import { Injectable } from '@angular/core'", '')
  .replace("@Injectable({ providedIn: 'root' })", '')
const browserCode = `${adapter}
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
const service = new SharedNodeConnectionService();
window.openControl = async () => {
  const ws = window.control = service.open('ws://' + location.host + '/ws?token=test', { kind: 'control', scope: 'device/alpha' });
  window.messages = [];
  ws.onmessage = event => window.messages.push(JSON.parse(event.data));
  await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error('control open timeout')), 10000); ws.onopen = () => { clearTimeout(timer); resolve(); }; ws.onerror = reject; });
  ws.send(JSON.stringify({type:'subscribe', topics:['node.status']}));
  return { shared: service.isShared(ws) };
};
window.openSync = async page => {
  const doc = window.doc = new Y.Doc();
  const provider = window.provider = new WebsocketProvider('ws://' + location.host + '/yws', 'alpha', doc, {
    params: { dev: 'browser', token: 'test', browser_page_id: page, client_yws_attempt_id: page },
    WebSocketPolyfill: service.syncWebSocket(), disableBc: true,
  });
  await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error('first sync timeout')), 10000); provider.on('sync', synced => { if (synced) { clearTimeout(timer); resolve(); } }); });
  return doc.getMap('data').toJSON();
};`
const { outputFiles } = await esbuild.build({ stdin: { contents: browserCode, loader: 'ts', resolveDir: client }, bundle: true, format: 'esm', write: false })
const script = outputFiles[0].text
const worker = await readFile(`${client}/src/assets/runtime/shared-node-connection.worker.js`)
let workerUnavailable = false
const server = createServer((request, response) => {
  if (request.url.includes('shared-node-connection.worker.js')) {
    response.writeHead(workerUnavailable ? 404 : 200, { 'Content-Type': 'text/javascript' })
    response.end(workerUnavailable ? '' : worker)
  } else if (request.url === '/app.js') {
    response.writeHead(200, { 'Content-Type': 'text/javascript' })
    response.end(script)
  } else {
    response.writeHead(200, { 'Content-Type': 'text/html' })
    response.end('<!doctype html><base href="/"><script type="module" src="/app.js"></script>')
  }
})
const wss = new WebSocketServer({ server })
const active = { control: new Set(), sync: new Set() }
const opens = { control: 0, sync: 0 }
const commands = []
const doc = new Y.Doc()
doc.getMap('data').set('boot', 'from-node')
doc.on('update', (update, origin) => {
  const encoder = encoding.createEncoder()
  encoding.writeVarUint(encoder, 0)
  sync.writeUpdate(encoder, update)
  for (const socket of active.sync) if (socket !== origin) socket.send(encoding.toUint8Array(encoder))
})
wss.on('connection', (socket, request) => {
  const kind = request.url.startsWith('/yws') ? 'sync' : 'control'
  opens[kind]++
  active[kind].add(socket)
  socket.on('close', () => active[kind].delete(socket))
  socket.on('message', bytes => {
    if (kind === 'control') {
      const message = JSON.parse(bytes)
      if (message.t === 'cmd') {
        commands.push(message.id)
        socket.send(JSON.stringify({ ch: 'events', t: 'ack', id: message.id, ok: true }))
      }
      return
    }
    const decoder = decoding.createDecoder(bytes)
    if (decoding.readVarUint(decoder) !== 0) return
    const encoder = encoding.createEncoder()
    encoding.writeVarUint(encoder, 0)
    sync.readSyncMessage(decoder, encoder, doc, socket)
    if (encoding.length(encoder) > 1) socket.send(encoding.toUint8Array(encoder))
  })
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const origin = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: process.env.ADAOS_E2E_BROWSER_EXECUTABLE || (process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined), headless: true })
try {
  const context = await browser.newContext()
  // Pooling is disabled in the shipped configuration; qualify it explicitly.
  await context.addInitScript(() => { window.__ADAOS_RUNTIME_CONFIG__ = { sharedNodeConnectionEnabled: true } })
  const alpha = await context.newPage()
  const builder = await context.newPage()
  for (const page of [alpha, builder]) {
    page.on('pageerror', error => console.log('BROWSER ERROR:', error.stack))
    page.on('console', message => { if (message.type() === 'error') console.log('CONSOLE ERROR:', message.text()) })
    await page.goto(origin + (page === alpha ? '/alpha/' : '/builder/'))
    await page.waitForFunction(() => typeof window.openControl === 'function')
    assert.equal((await page.evaluate(() => window.openControl())).shared, true)
  }
  assert.equal(opens.control, 1)
  const states = await Promise.all([alpha.evaluate(() => window.openSync('a')), builder.evaluate(() => window.openSync('b'))])
  assert.deepEqual(states, [{ boot: 'from-node' }, { boot: 'from-node' }])
  assert.equal(opens.sync, 1)
  await alpha.evaluate(() => window.doc.getMap('data').set('alpha', 1))
  await builder.waitForFunction(() => window.doc.getMap('data').get('alpha') === 1)
  await builder.evaluate(() => window.doc.getMap('data').set('builder', 2))
  await alpha.waitForFunction(() => window.doc.getMap('data').get('builder') === 2)
  await alpha.close()
  await builder.evaluate(() => window.control.send(JSON.stringify({ch:'events', t:'cmd', id:'after-alpha-close', kind:'mutation'})))
  await builder.waitForFunction(() => window.messages.some(message => message.id === 'after-alpha-close'))
  assert.equal(active.control.size, 1)
  assert.equal(active.sync.size, 1)
  assert.deepEqual(commands, ['after-alpha-close'])
  const fallback = await browser.newContext()
  await fallback.addInitScript(() => {
    window.__ADAOS_RUNTIME_CONFIG__ = { sharedNodeConnectionEnabled: true }
    Object.defineProperty(window, 'SharedWorker', { value: undefined })
  })
  const privatePage = await fallback.newPage()
  await privatePage.goto(origin)
  await privatePage.waitForFunction(() => typeof window.openControl === 'function')
  assert.equal((await privatePage.evaluate(() => window.openControl())).shared, false)
  await fallback.close()
  workerUnavailable = true
  const broken = await browser.newContext()
  await broken.addInitScript(() => { window.__ADAOS_RUNTIME_CONFIG__ = { sharedNodeConnectionEnabled: true } })
  const brokenPage = await broken.newPage()
  await brokenPage.goto(origin)
  await brokenPage.waitForFunction(() => typeof window.openControl === 'function')
  assert.equal((await brokenPage.evaluate(() => window.openControl())).shared, false)
  await broken.close()
  console.log(JSON.stringify({ result: 'passed', checks: ['two tabs: one control socket', 'two tabs: one Yjs socket', 'authoritative first sync', 'bidirectional edits without server echo', 'first tab closure', 'private fallback without SharedWorker', 'private fallback on worker load failure'], opens }))
} finally {
  await browser.close()
  for (const socket of wss.clients) socket.terminate()
  wss.close()
  server.close()
  doc.destroy()
}
