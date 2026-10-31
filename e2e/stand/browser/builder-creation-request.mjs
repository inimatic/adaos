import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import fs from 'node:fs/promises'
import path from 'node:path'

const input = JSON.parse(await fs.readFile(process.env.ADAOS_E2E_CREATION_INPUT, 'utf8'))
const {project,model,builderHost,prompt,timeout_seconds} = input
assert(/^test_creation_[a-f0-9]{18}$/.test(project))
const output=process.env.ADAOS_E2E_OUTPUT
await fs.mkdir(output,{recursive:true})
const token=process.env.ADAOS_E2E_HUB_TOKEN
assert(token)
const report={ok:false,submitted:false,errors:[],httpErrors:[],journals:[]}
const priorTerminalRequests = new Set()
const browser = await chromium.launch({ headless: true })
let page
const cached = new Map()

async function inspectJournals() {
  const root = path.resolve(input.state_dir,'builder/generation_journals')
  for (const name of await fs.readdir(root).catch(() => [])) {
    if (!/^[a-f0-9]{24}$/.test(name)) continue
    const directory = path.join(root, name)
    const owner = await fs.readFile(path.join(directory, 'owner.json'), 'utf8').then(JSON.parse).catch(() => null)
    if (owner?.source_scope?.object_id !== project) continue
    if (!report.journals.includes(name)) report.journals.push(name)
    for (const file of await fs.readdir(directory)) {
      if (!file.endsWith('.json')) continue
      const full = path.join(directory, file)
      const stamp = (await fs.stat(full)).mtimeMs
      if (cached.get(full) === stamp) continue
      cached.set(full, stamp)
      const value = await fs.readFile(full, 'utf8').then(JSON.parse).catch(() => null)
      if (value?.schema === 'adaos.builder.interpretation_attempt.v1'
          && !priorTerminalRequests.has(value.request_id)
          && ['failed', 'cancelled', 'submission_outcome_unknown', 'interpretation_rejected'].includes(value.status)) {
        report.failure = { request_id: value.request_id, status: value.status,
          findings: value.admission?.findings, provider_rejection: value.result?.telemetry?.provider_rejection }
      }
    }
  }
}

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'ru-RU' })
  await context.addInitScript(({value,hub,subnet}) => {
    for (const [key, item] of Object.entries({ adaos_local_hub_url: hub,
      adaos_local_hub_token: value, adaos_local_subnet_id: subnet,
      adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo', adaos_lang: 'ru' })) localStorage.setItem(key, item)
  }, {value:token,hub:process.env.ADAOS_E2E_HUB_URL,subnet:process.env.ADAOS_E2E_SUBNET_ID})
  page = await context.newPage()
  page.on('pageerror', error => report.errors.push(String(error)))
  page.on('response', async response => {
    if (response.status() >= 400) {
      const row = { url: response.url().split('?')[0], status: response.status() }
      if(new URL(response.url()).pathname==='/api/tools/call') {
        row.tool = response.request().postDataJSON()?.tool
        row.detail = (await response.text()).slice(0,2000)
      }
      report.httpErrors.push(row)
    }
  })
  await page.goto(`${process.env.ADAOS_E2E_CLIENT_URL}?intent=webspace.open&zone=lo&subnet_id=${process.env.ADAOS_E2E_SUBNET_ID}&webspace_id=${encodeURIComponent(builderHost)}&scenario_id=builder&expected_scenario_id=builder&try_local_hub=1`,
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForFunction(args => [...document.querySelectorAll('ada-chat-widget')].some(el => {
    if (!el.getClientRects().length) return false
    const c = window.ng?.getComponent(el), s = c?.pageState?.getSnapshot()
    return s?.selectedProjectId === args.project && c.selectedModel === 'chatgpt/' + args.model
  }), { project, model }, { timeout: 90000 })
  const chat = page.locator('ada-chat-widget').filter({ visible: true }).last()
  const read = () => chat.evaluate(el => {
    const c = window.ng.getComponent(el), s = c.pageState.getSnapshot()
    return { project: s.selectedProjectId, phase: s.workflowActivePhase,
      revision: s.workbench?.prototype_revision, model: c.selectedModel, messages: c.messages?.slice(-8) }
  })
  report.before = await read()
  assert(!report.before.revision,'A creation experiment must use a fresh unmaterialized project')
  await fs.writeFile(path.join(output,'chat-intent.json'),JSON.stringify({project,model,prompt,transport:'ordinary_builder_chat'},null,2),{flag:'wx'})
  const previous = new Set((report.before.messages || []).map(row => row.id))
  for (const selector of ['.pending-actions-panel__header button', '.component-updates-backdrop']) {
    const close = page.locator(selector).filter({ visible: true }).last()
    if (await close.isVisible()) await close.dispatchEvent('click')
  }
  await chat.locator('textarea').fill(prompt)
  await chat.locator('textarea').press('Control+Enter')
  report.submitted = true
  report.submitted_at = new Date().toISOString()
  console.log(JSON.stringify({ event: 'submitted-once', project, model }))
  const started = Date.now()
  while (Date.now() - started < timeout_seconds * 1000) {
    report.after = await read()
    assert.equal(report.after.project, project, 'Selected project changed; do not attribute another result')
    await inspectJournals()
    if (report.after.revision && report.after.revision==='001') { report.ok = true; break }
    if (report.failure) break
    const terminal = report.after.messages?.find(row => !previous.has(row.id) && row.from !== 'user'
      && /TaskInterpretationRejected|task\.|prototype_.*failed|Не удалось|не смог|could not complete|ai_.*required|builder_user_turn_not_acknowledged|budget_exhausted/.test(row.text || ''))
    if (terminal) { report.failure = { detail: terminal.text }; break }
    report.elapsed_ms=Date.now()-started
    await page.waitForTimeout(2000)
  }
  report.elapsed_ms=Date.now()-started
  await page.screenshot({ path: path.join(output, 'builder-result.png'), fullPage: true })
} catch (error) {
  report.errors.push(String(error))
  if (page) await page.screenshot({ path: path.join(output, 'browser-error.png') }).catch(() => {})
} finally {
  await fs.writeFile(path.join(output, 'chat-report.json'), JSON.stringify(report, null, 2), { flag: 'wx' })
  await browser.close()
}
console.log(JSON.stringify({ ok: report.ok, submitted: report.submitted, errors: report.errors,
  revision: report.after?.revision, journals: report.journals, failure: report.failure }))
if (!report.ok) process.exitCode = 1
