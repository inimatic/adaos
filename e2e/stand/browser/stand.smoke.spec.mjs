import fs from 'node:fs'
import path from 'node:path'
import { expect, test } from '@playwright/test'

const bundleRoot = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'artifacts/e2e-runs/browser-local')
const clientRoot = path.join(bundleRoot, 'client')
const runId = String(process.env.ADAOS_E2E_RUN_ID || 'browser-local')
const target = {
  hubUrl: String(process.env.ADAOS_E2E_HUB_URL || ''),
  subnetId: String(process.env.ADAOS_E2E_SUBNET_ID || ''),
  webspaceId: String(process.env.ADAOS_E2E_WEBSPACE_ID || 'desktop'),
  browserDeviceId: String(process.env.ADAOS_E2E_BROWSER_DEVICE_ID || 'e2e-browser-01'),
}

fs.mkdirSync(clientRoot, { recursive: true })

function redactUrl(value) {
  try {
    const url = new URL(String(value))
    for (const key of ['token', 'access_token', 'refresh_token', 'jwt', 'session']) {
      if (url.searchParams.has(key)) url.searchParams.set(key, '[REDACTED]')
    }
    return url.toString()
  } catch {
    return String(value)
  }
}

function redactText(value) {
  return String(value || '')
    .replace(/\bBearer\s+[^\s,;]+/gi, 'Bearer [REDACTED]')
    .replace(/([?&](?:access_token|refresh_token|token|jwt|session)=)[^&#\s]+/gi, '$1[REDACTED]')
    .replace(/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b/g, '[REDACTED]')
}

function appendRecord(fileName, kind, payload = {}) {
  const record = {
    schema: 'adaos.e2e.browser-event.v1',
    run_id: runId,
    observed_at: new Date().toISOString(),
    kind,
    ...payload,
  }
  fs.appendFileSync(path.join(clientRoot, fileName), `${JSON.stringify(record)}\n`, 'utf8')
}

async function readDebugState(page) {
  return page.evaluate(() => {
    const reader = window.__ADAOS_DEBUG_STATE__
    return typeof reader === 'function' ? reader() : null
  })
}

test('deployed browser reaches connected materialized AdaOS state', async ({ page }) => {
  const fatalConsole = []
  const pageErrors = []

  page.on('console', (message) => {
    const text = redactText(message.text())
    appendRecord('console.ndjson', 'console', { level: message.type(), text })
    if (message.type() === 'error') fatalConsole.push(text)
  })
  page.on('pageerror', (error) => {
    const text = redactText(error?.stack || error?.message || error)
    pageErrors.push(text)
    appendRecord('console.ndjson', 'pageerror', { level: 'error', text })
  })
  page.on('response', (response) => {
    appendRecord('network.ndjson', 'response', {
      method: response.request().method(),
      resource_type: response.request().resourceType(),
      status: response.status(),
      url: redactUrl(response.url()),
    })
  })
  page.on('requestfailed', (request) => {
    appendRecord('network.ndjson', 'request_failed', {
      method: request.method(),
      resource_type: request.resourceType(),
      url: redactUrl(request.url()),
      error: redactText(request.failure()?.errorText || 'unknown'),
    })
  })
  page.on('websocket', (socket) => {
    const url = redactUrl(socket.url())
    appendRecord('ws-lifecycle.ndjson', 'websocket_opened', { url })
    socket.on('framesent', (event) => appendRecord('ws-lifecycle.ndjson', 'websocket_frame_sent', {
      url,
      bytes: Buffer.byteLength(event.payload),
    }))
    socket.on('framereceived', (event) => appendRecord('ws-lifecycle.ndjson', 'websocket_frame_received', {
      url,
      bytes: Buffer.byteLength(event.payload),
    }))
    socket.on('socketerror', (error) => appendRecord('ws-lifecycle.ndjson', 'websocket_error', {
      url,
      error: redactText(error),
    }))
    socket.on('close', () => appendRecord('ws-lifecycle.ndjson', 'websocket_closed', { url }))
  })

  await page.addInitScript((metadata) => {
    localStorage.setItem('adaos_device_id', metadata.browserDeviceId)
    localStorage.setItem('adaos_webspace_id', metadata.webspaceId)
    localStorage.setItem('adaos_hub_base', metadata.hubUrl)
    localStorage.setItem('adaos_try_local_hub', '0')
    localStorage.setItem('adaos_local_subnet_id', metadata.subnetId)
    window.__ADAOS_DEBUG__ = true
  }, target)

  try {
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('body')).toBeVisible()
    await expect.poll(async () => Boolean(await readDebugState(page))).toBe(true)
    await expect.poll(async () => (await readDebugState(page))?.sync?.connectionState || 'missing').toBe('connected')
    await expect.poll(async () => Boolean((await readDebugState(page))?.sync?.materializationReady)).toBe(true)

    const debugState = await readDebugState(page)
    fs.writeFileSync(path.join(clientRoot, 'debug-state.json'), `${JSON.stringify(debugState, null, 2)}\n`, 'utf8')
    await page.screenshot({ path: path.join(clientRoot, 'stand-smoke.png'), fullPage: true })

    expect(pageErrors, `fatal page errors: ${pageErrors.join(' | ')}`).toEqual([])
    expect(fatalConsole, `fatal console errors: ${fatalConsole.join(' | ')}`).toEqual([])
  } finally {
    const state = await readDebugState(page).catch(() => null)
    fs.writeFileSync(
      path.join(clientRoot, 'browser-summary.json'),
      `${JSON.stringify({ run_id: runId, fatal_console: fatalConsole, page_errors: pageErrors, debug_state: state }, null, 2)}\n`,
      'utf8',
    )
  }
})
