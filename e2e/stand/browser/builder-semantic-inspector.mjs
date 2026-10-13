// Inspect exact source refs/write set through the installed Builder UI. No writes.
import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal } from './prototype-navigation.mjs'

const expect = baseExpect.configure({timeout: 30_000})
const output = process.env.ADAOS_E2E_OUTPUT
const project = process.env.ADAOS_E2E_SCENARIO_ID
const host = process.env.ADAOS_E2E_HOST
const revision = process.env.ADAOS_E2E_EXPECTED_REVISION
const hub = process.env.ADAOS_E2E_HUB_URL, token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const report = {ok: false, project, host, revision, reads: 0, beforeOpenReads: 0, checks: [], errors: [], modelCalls: 0}
const browser = await chromium.launch({headless: true})
const context = await browser.newContext({viewport: process.env.ADAOS_E2E_COMPACT === '1'
  ? {width: 390, height: 844} : {width: 1440, height: 1000}})
await context.addInitScript(({hub, token, subnet}) => {
  window.__ADAOS_DEBUG__ = true; window.__ADAOS_BASE__ = hub; window.__ADAOS_TOKEN__ = token
  for (const [key, value] of Object.entries({adaos_device_id: 'e2e-semantic-inspector', adaos_hub_base: hub,
    adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token, adaos_local_subnet_id: subnet,
    adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'en'})) localStorage.setItem(key, value)
}, {hub, token, subnet})
const page = await context.newPage()
let packet
function findPacket(value, depth = 0) {
  if (!value || typeof value !== 'object' || depth > 6) return
  if (value.schema === 'adaos.builder.semantic_inspector.v1') return value
  for (const child of Object.values(value)) {
    const found = findPacket(child, depth + 1)
    if (found) return found
  }
}
page.on('request', request => {
  if (request.url().endsWith('/api/tools/call')) {
    try {if (/^builder_sdk_control_skill[:.]get_semantic_inspector$/.test(request.postDataJSON()?.tool || '')) report.reads++} catch {}
  }
})
page.on('response', async response => {
  try {
    if (!response.url().endsWith('/api/tools/call')) return
    if (!/^builder_sdk_control_skill[:.]get_semantic_inspector$/.test(response.request().postDataJSON()?.tool || '')) return
    packet = findPacket(await response.json())
  } catch {}
})
page.on('pageerror', error => report.errors.push(String(error)))
try {
  await installSystemOverlayDismissal(page)
  const url = new URL(process.env.ADAOS_E2E_CLIENT_URL)
  for (const [key, value] of Object.entries({intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
    webspace_id: host, expected_scenario_id: 'builder', try_local_hub: '1'})) url.searchParams.set(key, value)
  await page.goto(url.href, {waitUntil: 'domcontentloaded', timeout: 60_000})
  await page.waitForFunction(() => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === 'builder', {timeout: 60_000})
  let result = page.locator('[data-command-id="result"]').filter({visible: true}).first()
  if (!await result.isVisible()) {
    await page.locator('[data-webui-widget-id="design-workbench-header"] [data-command-id="inspect-section"]').click()
    result = page.locator('[data-command-option="result"]').filter({visible: true}).first()
  }
  await result.click()
  const button = page.locator('[data-webui-widget-id="design-preview-commands"] [data-command-id="semantic-inspector"]')
  await expect(button).toBeVisible()
  await expect(button).toContainText('Semantics')
  report.beforeOpenReads = report.reads
  if (report.beforeOpenReads) throw new Error('Inspector must not load before explicit opening')
  await button.click()
  const summary = page.locator('[data-webui-widget-id="design-semantic-summary"]').filter({visible: true}).last()
  const writes = page.locator('[data-webui-widget-id="design-semantic-writes"]').filter({visible: true}).last()
  const refs = page.locator('[data-webui-widget-id="design-semantic-refs"]').filter({visible: true}).last()
  await expect(summary).toBeVisible()
  await expect(summary).toContainText(`scenario:${project}`)
  await expect.poll(() => packet?.status).toBe('verified')
  if (packet.source_revision !== revision || packet.project_ref !== `scenario:${project}`) throw new Error('Inspector source identity mismatch')
  if (report.reads !== 1) throw new Error(`Expected one on-demand inspection, received ${report.reads}`)
  await expect(writes).toContainText('view:records-list')
  await expect(writes).toContainText('field_refs')
  if (packet.write_set.length !== 1 || packet.write_set[0].property !== 'field_refs') throw new Error('Inspector write set is not the exact bounded property')
  await refs.locator('input[type="search"], input[placeholder="Search"], input').first().fill('view:records-list')
  await expect(refs).toContainText('view:records-list')
  await expect(refs).toContainText('field_refs')
  report.packet = packet
  report.checks.push('EN captions', 'no eager inspection', 'one read for three projections',
    'exact current revision', 'separate operation base', 'actual field_refs write', 'searchable semantic refs')
  await page.screenshot({path: path.join(output, 'inspector.png'), fullPage: true})
  await page.keyboard.press('Escape')
  await expect(summary).toBeHidden()
  report.checks.push('inspector closes without Preview navigation')
  report.ok = report.errors.length === 0
} catch (error) {report.error = String(error)}
finally {
  await fs.mkdir(output, {recursive: true})
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({...report, packet: undefined}))
  await browser.close()
}
if (!report.ok) process.exitCode = 1
