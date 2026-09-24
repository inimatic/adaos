import { chromium } from '@playwright/test'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'

if (process.env.ENV_TYPE !== 'dev') {
  throw new Error('Builder candidate browser feedback requires ENV_TYPE=dev')
}

const scenario = String(process.env.ADAOS_E2E_SCENARIO_ID || '').trim()
const webspace = String(process.env.ADAOS_E2E_WEBSPACE_ID || '').trim()
const subnet = String(process.env.ADAOS_E2E_SUBNET_ID || '').trim()
const token = String(process.env.ADAOS_E2E_HUB_TOKEN || '').trim()
const output = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'e2e/artifacts/builder-browser-feedback')
const hub = String(process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777').replace(/\/$/, '')
const client = String(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
const sourceDigest = String(process.env.ADAOS_E2E_SOURCE_DIGEST || '').trim()
const spaceKind = String(process.env.ADAOS_E2E_SPACE_KIND || 'development').trim()
const timeoutMs = Number(process.env.ADAOS_E2E_TIMEOUT_MS || 90_000)
const startupTimeoutMs = Math.min(timeoutMs, 90_000)
const interactionTimeoutMs = Math.min(timeoutMs, 10_000)
const commandSequence = String(process.env.ADAOS_E2E_COMMAND_SEQUENCE || '').split(',')
  .map(value => value.trim()).filter(Boolean).map(value => {
    const [command, option] = value.split(':', 2).map(part => part.trim())
    return { command, option: option || null }
  })

if (!scenario || !webspace || !subnet || !token) {
  throw new Error('Scenario, paired DEV webspace, subnet, and local control token are required')
}

await fs.mkdir(output, { recursive: true })
const url = new URL(client)
for (const [key, value] of Object.entries({
  intent: 'webspace.open',
  zone: 'lo',
  subnet_id: subnet,
  webspace_id: webspace,
  space_kind: spaceKind,
  expected_scenario_id: scenario,
  try_local_hub: '1',
  runtime_debug: '1',
})) url.searchParams.set(key, value)

const report = {
  schema: 'adaos.builder.browser_feedback.v1',
  scenario_id: scenario,
  webspace_id: webspace,
  space_kind: spaceKind,
  source_digest: sourceDigest || null,
  runtime_url: hub,
  client_url: client,
  samples: [],
  passed: false,
}
const browser = await chromium.launch({ headless: true })

const blockingTextPatterns = [
  /renderer failed to load/i,
  /scenario not found/i,
  /installed skill runtime is out of sync/i,
  /showing the last successful data while the source reconnects/i,
]

const pendingDataStates = new Set(['idle', 'loading', 'refreshing'])
const nonAuthoritativeDataStates = new Set(['stale', 'unavailable', 'error'])
const runtimeDataKinds = new Set(['skill', 'api', 'mcp', 'resourceQuery'])

const sensitiveDiagnosticKey = /(authorization|cookie|credential|password|secret|token|value)/i

function boundedDiagnosticFields(value, depth = 0) {
  if (!value || typeof value !== 'object' || depth > 1) return undefined
  const result = {}
  for (const [key, raw] of Object.entries(value).slice(0, 16)) {
    if (sensitiveDiagnosticKey.test(key)) {
      result[key] = '[redacted]'
    } else if (['string', 'boolean', 'number'].includes(typeof raw)) {
      result[key] = typeof raw === 'string' ? raw.slice(0, 512) : raw
    } else if (Array.isArray(raw)) {
      result[key] = raw.slice(0, 8)
        .filter(item => ['string', 'boolean', 'number'].includes(typeof item))
        .map(item => typeof item === 'string' ? item.slice(0, 256) : item)
    } else if (raw && typeof raw === 'object') {
      result[key] = boundedDiagnosticFields(raw, depth + 1)
    }
  }
  return result
}

function boundedToolFailureDiagnostic(request, response, bodyText) {
  let tool = null
  let argumentsProjection
  let contextProjection
  try {
    const payload = JSON.parse(request.postData() || '{}')
    const target = payload?.target ?? payload?.tool
    tool = typeof target === 'string' ? target.slice(0, 256) : null
    argumentsProjection = boundedDiagnosticFields(payload?.params ?? payload?.arguments)
    contextProjection = boundedDiagnosticFields(payload?.context)
  } catch {}
  let body = null
  try {
    body = JSON.parse(bodyText)
  } catch {
    body = bodyText
  }
  const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : body
  const diagnostic = { tool }
  if (argumentsProjection && Object.keys(argumentsProjection).length) {
    diagnostic.arguments = argumentsProjection
  }
  if (contextProjection && Object.keys(contextProjection).length) {
    diagnostic.context = contextProjection
  }
  if (typeof detail === 'string') {
    diagnostic.detail = detail.slice(0, 2048)
  } else if (detail && typeof detail === 'object') {
    const layers = [
      detail,
      detail.toolResult,
      detail.toolResult?.detail,
      detail.technical_detail,
    ].filter(layer => layer && typeof layer === 'object')
    for (const layer of layers) {
      for (const key of ['ok', 'status', 'error', 'reason', 'message', 'permission_id', 'application_id', 'retryable']) {
        if (diagnostic[key] === undefined && ['string', 'boolean', 'number'].includes(typeof layer[key])) {
          diagnostic[key] = typeof layer[key] === 'string'
            ? layer[key].slice(0, 2048)
            : layer[key]
        }
      }
    }
    if (typeof detail.detail === 'string') diagnostic.detail = detail.detail.slice(0, 2048)
    if (typeof detail.technical_detail?.tool === 'string') {
      diagnostic.technical_tool = detail.technical_detail.tool.slice(0, 256)
    }
  }
  const headers = response.headers()
  const trace = String(headers['x-adaos-trace'] || headers['x-request-id'] || '').trim()
  if (trace) diagnostic.trace_id = trace.slice(0, 256)
  return diagnostic
}

function formatToolFailureDiagnostic(diagnostic) {
  if (!diagnostic || typeof diagnostic !== 'object') return ''
  const parts = []
  for (const key of ['tool', 'error', 'reason', 'message', 'detail', 'trace_id']) {
    if (diagnostic[key] !== null && diagnostic[key] !== undefined && diagnostic[key] !== '') {
      parts.push(`${key}=${String(diagnostic[key])}`)
    }
  }
  if (diagnostic.arguments) parts.push(`arguments=${JSON.stringify(diagnostic.arguments)}`)
  if (diagnostic.context) parts.push(`context=${JSON.stringify(diagnostic.context)}`)
  return parts.length ? ` [${parts.join(' ')}]` : ''
}

async function waitForAuthoritativeData(page, sample, checkpoint) {
  const settleTimeoutMs = Math.min(timeoutMs, checkpoint === 'initial' ? 30_000 : 12_000)
  try {
    await page.waitForFunction(({ pending, runtime }) => {
      const visible = element => {
        const style = getComputedStyle(element)
        return element.getClientRects().length > 0
          && style.visibility !== 'hidden'
          && style.display !== 'none'
      }
      const widgets = [...document.querySelectorAll('[data-webui-data-state]')].filter(visible)
      return widgets.every(element => {
        const state = String(element.getAttribute('data-webui-data-state') || 'idle')
        const kind = String(element.getAttribute('data-webui-data-kind') || '')
        const hasValue = element.getAttribute('data-webui-data-has-value') === 'true'
        return !pending.includes(state) && !(runtime.includes(kind) && state === 'ready' && !hasValue)
      })
    }, { pending: [...pendingDataStates], runtime: [...runtimeDataKinds] }, { timeout: settleTimeoutMs })
  } catch {
    // Record the concrete widget states below instead of losing the useful evidence
    // behind a generic Playwright timeout.
  }

  const states = await page.evaluate(() => {
    const visible = element => {
      const style = getComputedStyle(element)
      return element.getClientRects().length > 0
        && style.visibility !== 'hidden'
        && style.display !== 'none'
    }
    return [...document.querySelectorAll('[data-webui-data-state]')]
      .filter(visible)
      .map(element => ({
        id: element.getAttribute('data-webui-widget-id') || 'unknown',
        kind: element.getAttribute('data-webui-data-kind') || 'unknown',
        state: element.getAttribute('data-webui-data-state') || 'idle',
        has_value: element.getAttribute('data-webui-data-has-value'),
      }))
  })
  const pending = states.filter(item => pendingDataStates.has(item.state)
    || (runtimeDataKinds.has(item.kind) && item.state === 'ready' && item.has_value !== 'true'))
  const nonAuthoritative = states.filter(item => nonAuthoritativeDataStates.has(item.state))
  sample.checks.push({
    kind: 'authoritative-data-settlement',
    checkpoint,
    widgets: states.length,
    states,
  })
  if (pending.length) {
    sample.hard_failures.push(
      `Data did not settle at ${checkpoint}: ${pending.map(item => `${item.id}=${item.state}`).join(', ')}`,
    )
  }
  if (nonAuthoritative.length) {
    sample.hard_failures.push(
      `Data is not authoritative at ${checkpoint}: ${nonAuthoritative.map(item => `${item.id}=${item.state}`).join(', ')}`,
    )
  }
  return pending.length === 0 && nonAuthoritative.length === 0
}

try {
  const layouts = {
    wide: { width: 1440, height: 1000 },
    compact: { width: 390, height: 844 },
  }
  for (const [layout, viewport] of Object.entries(layouts)) {
    console.error(`[builder-browser-feedback] ${layout}:context:start`)
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({
        adaos_device_id: 'builder-browser-feedback',
        adaos_webspace_id: webspace,
        adaos_hub_base: hub,
        adaos_local_hub_base: hub,
        adaos_try_local_hub: '1',
        adaos_hub_token: token,
        adaos_local_subnet_id: subnet,
        adaos_selected_zone: 'lo',
        adaos_last_used_zone: 'lo',
        adaos_env_type: 'dev',
      })) localStorage.setItem(key, value)
    }, { hub, token, subnet, webspace })

    const page = await context.newPage()
    page.setDefaultTimeout(interactionTimeoutMs)
    page.setDefaultNavigationTimeout(startupTimeoutMs)
    const sample = {
      layout,
      viewport,
      checks: [],
      page_errors: [],
      console_errors: [],
      request_failures: [],
      tool_calls: [],
      hard_failures: [],
      warnings: [],
    }
    report.samples.push(sample)
    const responseDiagnosticTasks = []
    const toolRequestStartedAt = new WeakMap()
    page.on('request', request => {
      try {
        if (new URL(request.url()).pathname === '/api/tools/call') {
          toolRequestStartedAt.set(request, Date.now())
        }
      } catch {}
    })
    page.on('pageerror', error => sample.page_errors.push(String(error.message || error)))
    page.on('console', message => {
      if (message.type() === 'error') sample.console_errors.push(message.text())
    })
    page.on('response', response => {
      const request = response.request()
      let target
      try {
        target = new URL(response.url())
      } catch {
        return
      }
      if (target.pathname === '/api/tools/call') {
        const startedAt = toolRequestStartedAt.get(request)
        responseDiagnosticTasks.push(
          response.text()
            .then(bodyText => {
              const diagnostic = boundedToolFailureDiagnostic(request, response, bodyText)
              sample.tool_calls.push({
                ...diagnostic,
                http_status: response.status(),
                duration_ms: startedAt ? Date.now() - startedAt : null,
              })
            })
            .catch(() => {}),
        )
      }
      if (response.status() < 400 || !['document', 'fetch', 'xhr'].includes(request.resourceType())) return
      const failure = {
        method: request.method(),
        status: response.status(),
        url: response.url(),
      }
      sample.request_failures.push(failure)
      if (target.pathname === '/api/tools/call') {
        responseDiagnosticTasks.push(
          response.text()
            .then(bodyText => {
              failure.diagnostic = boundedToolFailureDiagnostic(request, response, bodyText)
            })
            .catch(() => {}),
        )
      }
    })
    page.on('requestfailed', request => {
      sample.request_failures.push({
        method: request.method(),
        status: 0,
        url: request.url(),
        error: request.failure()?.errorText || 'request_failed',
      })
    })

    try {
      console.error(`[builder-browser-feedback] ${layout}:navigation:start`)
      await page.goto(url.href, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(expected => {
        const sync = window.__ADAOS_DEBUG_STATE__?.()?.sync
        return sync?.materializationReady && sync.materialization?.currentScenario === expected
      }, scenario, { timeout: startupTimeoutMs })
      await page.waitForFunction(() => {
        return [...document.querySelectorAll('[data-webui-widget-id]')]
          .some(element => element.getClientRects().length > 0)
      }, undefined, { timeout: startupTimeoutMs })
      console.error(`[builder-browser-feedback] ${layout}:authoritative:initial`)
      let authoritativeDataSettled = await waitForAuthoritativeData(page, sample, 'initial')
      await page.screenshot({ path: path.join(output, `${layout}-initial.png`), fullPage: true })
      console.error(`[builder-browser-feedback] ${layout}:initial:captured`)

      for (const step of commandSequence) {
        const command = page.locator(`[data-command-id="${step.command}"]`).filter({ visible: true }).first()
        await command.waitFor({ state: 'visible', timeout: interactionTimeoutMs })
        await command.click({ timeout: interactionTimeoutMs })
        if (step.option) {
          const option = page.locator(`[data-command-option="${step.option}"]`).filter({ visible: true }).first()
          await option.waitFor({ state: 'visible', timeout: interactionTimeoutMs })
          await option.click({ timeout: interactionTimeoutMs })
        }
        authoritativeDataSettled = await waitForAuthoritativeData(
          page,
          sample,
          `command:${step.command}`,
        ) && authoritativeDataSettled
        sample.checks.push({ kind: 'command-sequence', command: step.command, option: step.option })
        const activeModal = page.locator('ion-modal.show-modal').last()
        if (await activeModal.count()) {
          const modalDiagnostics = await activeModal.evaluate(element => {
            const visible = child => {
              const style = getComputedStyle(child)
              return child.getClientRects().length > 0
                && style.visibility !== 'hidden'
                && style.display !== 'none'
            }
            const widgets = [...element.querySelectorAll('[data-webui-widget-id]')].filter(visible)
            return {
              id: element.id || null,
              schema: Boolean(element.querySelector('ada-schema-modal')),
              title: String(element.querySelector('ion-title')?.textContent || '').replace(/\s+/g, ' ').trim(),
              visible_widget_ids: widgets
                .map(widget => widget.getAttribute('data-webui-widget-id'))
                .filter(Boolean),
            }
          })
          sample.checks.push({
            kind: 'modal-surface',
            command: step.command,
            ...modalDiagnostics,
          })
          if (modalDiagnostics.schema && !modalDiagnostics.visible_widget_ids.length) {
            sample.hard_failures.push(
              `Schema modal opened by ${step.command} has no visible widgets`,
            )
          }
          await page.screenshot({
            path: path.join(output, `${layout}-command-${step.command}.png`),
            fullPage: true,
          })
        }
      }

      const selectableSelector = [
        'tr.row-selectable',
        'tr.is-selectable',
        'button.note-card-main',
        'ion-item.collection-focus-item:not([disabled])',
        '.tree-widget__node.is-selectable',
      ].join(', ')

      let compactSelectionRegion = null
      const activeModal = page.locator('ion-modal.show-modal').last()
      const interactionRoot = await activeModal.count() ? activeModal : page
      if (layout === 'compact') {
        const compactTriggers = interactionRoot.locator('.layout-region-trigger')
        const compactTriggerCount = await compactTriggers.count()
        if (compactTriggerCount) {
          for (let triggerIndex = 0; triggerIndex < compactTriggerCount; triggerIndex += 1) {
            const trigger = compactTriggers.nth(triggerIndex)
            if ((await trigger.getAttribute('aria-expanded')) !== 'true') {
              await trigger.click({ timeout: interactionTimeoutMs })
              await page.waitForTimeout(250)
            }
            const openRegion = interactionRoot.locator('ada-layout-region.is-open').first()
            if ((await trigger.getAttribute('aria-expanded')) !== 'true' || !(await openRegion.count())) {
              sample.hard_failures.push(`Compact disclosure ${triggerIndex + 1} did not open its semantic region`)
              continue
            }
            if (triggerIndex === 0) {
              sample.checks.push({ kind: 'compact-region-disclosure', count: compactTriggerCount })
              await page.screenshot({ path: path.join(output, `${layout}-disclosure.png`), fullPage: true })
            }
            const close = openRegion.locator('.layout-region__header button').first()
            if (await close.count()) {
              await close.click({ timeout: interactionTimeoutMs })
              await page.waitForTimeout(150)
              if ((await trigger.getAttribute('aria-expanded')) !== 'false') {
                sample.hard_failures.push(`Compact disclosure ${triggerIndex + 1} did not close`)
              } else if (!(await trigger.evaluate(element => element === document.activeElement))) {
                sample.hard_failures.push(`Compact disclosure ${triggerIndex + 1} did not restore trigger focus`)
              } else {
                await trigger.click({ timeout: interactionTimeoutMs })
                await page.waitForTimeout(150)
                if ((await trigger.getAttribute('aria-expanded')) !== 'true') {
                  sample.hard_failures.push(`Compact disclosure ${triggerIndex + 1} did not reopen`)
                }
              }
            }
            const reopened = interactionRoot.locator('ada-layout-region.is-open').first()
            try {
              await reopened.locator(selectableSelector).filter({ visible: true }).first()
                .waitFor({ state: 'visible', timeout: Math.min(timeoutMs, 5_000) })
            } catch {
              // Empty collections are valid and remain visible for diagnostics.
            }
            const isCollectionRegion = (await reopened.getAttribute('data-region-role')) === 'collection'
            const candidates = isCollectionRegion
              ? reopened.locator(selectableSelector)
              : reopened.locator('__adaos_no_primary_selection__')
            const candidateCount = await candidates.count()
            let hasVisibleCandidate = false
            for (let index = 0; index < candidateCount; index += 1) {
              if (await candidates.nth(index).isVisible()) {
                hasVisibleCandidate = true
                break
              }
            }
            if (hasVisibleCandidate) {
              compactSelectionRegion = reopened
              break
            }
            if (triggerIndex < compactTriggerCount - 1 && await close.count()) {
              await reopened.locator('.layout-region__header button').first().click({ timeout: interactionTimeoutMs })
              await page.waitForTimeout(150)
            } else {
              compactSelectionRegion = reopened
            }
          }
        }
      }

      const collectionRegions = interactionRoot.locator(
        'ada-layout-region[data-region-role="collection"]',
      ).filter({ visible: true })
      let selectableItems = collectionRegions.locator(selectableSelector)
      if (compactSelectionRegion) selectableItems = compactSelectionRegion.locator(selectableSelector)
      const selectableCount = await selectableItems.count()
      for (let index = 0; index < selectableCount; index += 1) {
        const item = selectableItems.nth(index)
        if (!(await item.isVisible())) continue
        console.error(`[builder-browser-feedback] ${layout}:selection:${index + 1}:start`)
        try {
          await item.focus({ timeout: interactionTimeoutMs })
          await item.click({ timeout: interactionTimeoutMs })
        } catch (error) {
          sample.hard_failures.push(
            `Primary selection could not be activated: ${String(error?.message || error)}`,
          )
          console.error(`[builder-browser-feedback] ${layout}:selection:${index + 1}:failed`)
          break
        }
        authoritativeDataSettled = await waitForAuthoritativeData(
          page,
          sample,
          'primary-selection',
        ) && authoritativeDataSettled
        sample.checks.push({ kind: 'primary-selection', count: selectableCount })
        await page.screenshot({ path: path.join(output, `${layout}-selection.png`), fullPage: true })
        console.error(`[builder-browser-feedback] ${layout}:selection:${index + 1}:captured`)
        break
      }

      const semanticTabList = interactionRoot.locator(
        '[data-webui-widget-type="navigation.tabs"] [role="tablist"]',
      ).filter({ visible: true }).first()
      const semanticTabs = semanticTabList.locator('[role="tab"]')
      const tabCount = await semanticTabs.count()
      const tabDescriptors = []
      for (let index = 0; index < Math.min(tabCount, 12); index += 1) {
        const tab = semanticTabs.nth(index)
        if (!(await tab.isVisible())) continue
        const descriptor = await tab.evaluate(element => ({
          controls: element.getAttribute('aria-controls'),
          id: element.id,
          text: String(element.textContent || '').replace(/\s+/g, ' ').trim(),
        }))
        tabDescriptors.push({ ...descriptor, index })
      }
      for (let index = 0; index < tabDescriptors.length; index += 1) {
        const descriptor = tabDescriptors[index]
        const stableTab = semanticTabList.locator('[role="tab"]').nth(descriptor.index)
        if (!(await stableTab.count()) || !(await stableTab.isVisible())) continue
        if (await stableTab.isDisabled() || (await stableTab.getAttribute('aria-disabled')) === 'true') continue
        const handle = await stableTab.elementHandle()
        if (!handle) continue
        console.error(`[builder-browser-feedback] ${layout}:tab:${index + 1}:start`)
        try {
          await handle.focus()
          await handle.press('Enter', { timeout: interactionTimeoutMs })
        } catch (error) {
          sample.hard_failures.push(
            `Semantic tab ${index + 1} could not be activated: ${String(error?.message || error)}`,
          )
          console.error(`[builder-browser-feedback] ${layout}:tab:${index + 1}:failed`)
          continue
        }
        authoritativeDataSettled = await waitForAuthoritativeData(
          page,
          sample,
          `tab:${index + 1}`,
        ) && authoritativeDataSettled
        const selected = await stableTab.getAttribute('aria-selected')
        if (selected !== 'true') {
          sample.hard_failures.push(`Semantic tab ${index + 1} did not become selected`)
        }
        if (index < 6) {
          await page.screenshot({ path: path.join(output, `${layout}-tab-${index + 1}.png`), fullPage: true })
        }
        console.error(`[builder-browser-feedback] ${layout}:tab:${index + 1}:captured`)
      }
      if (tabCount) sample.checks.push({ kind: 'semantic-tabs', count: tabCount })
      sample.authoritative_data_settled = authoritativeDataSettled

      const diagnostics = await page.evaluate(patternSources => {
        const visible = element => {
          const style = getComputedStyle(element)
          return element.getClientRects().length > 0 && style.visibility !== 'hidden' && style.display !== 'none'
        }
        const activeModal = [...document.querySelectorAll('ion-modal.show-modal')].filter(visible).at(-1)
        const diagnosticRoot = activeModal || document
        const widgetIds = [...diagnosticRoot.querySelectorAll('[data-webui-widget-id]')]
          .filter(visible)
          .map(element => element.getAttribute('data-webui-widget-id'))
          .filter(Boolean)
        const unlabeled = [...diagnosticRoot.querySelectorAll('button, a[href], input, select, textarea, [role="button"]')]
          .filter(visible)
          .filter(element => {
            const text = String(element.innerText || element.value || '').trim()
            const labels = 'labels' in element && element.labels ? element.labels.length : 0
            return !text
              && !labels
              && !element.getAttribute('aria-label')
              && !element.getAttribute('aria-labelledby')
              && !element.getAttribute('title')
              && !element.getAttribute('placeholder')
          })
          .slice(0, 24)
          .map(element => ({
            tag: element.tagName.toLowerCase(),
            role: element.getAttribute('role'),
            widget: element.closest('[data-webui-widget-id]')?.getAttribute('data-webui-widget-id') || null,
          }))
        const rendererFailures = [...diagnosticRoot.querySelectorAll(
          '[data-webui-render-state="error"], [data-webui-render-state="unsupported"]',
        )].map(element => ({
          id: element.getAttribute('data-webui-widget-id'),
          type: element.getAttribute('data-webui-widget-type'),
          state: element.getAttribute('data-webui-render-state'),
          text: String(element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 500),
        }))
        const visibleRegionElements = [...diagnosticRoot.querySelectorAll(
          'ada-layout-region[data-region-role]',
        )].filter(visible)
        const visibleRegions = visibleRegionElements.map(element => {
            const box = element.getBoundingClientRect()
            const gridBox = element.closest('.desktop-grid')?.getBoundingClientRect()
            return {
              id: element.getAttribute('data-region-id'),
              role: element.getAttribute('data-region-role'),
              compact_presentation: element.getAttribute('data-compact-presentation'),
              top: box.top,
              bottom: box.bottom,
              width: box.width,
              grid_width: gridBox?.width || null,
            }
          })
        const bodyRoles = new Set(['collection', 'main', 'detail', 'inspector'])
        const toolbarAfterBody = visibleRegionElements.some(toolbar => {
          if (toolbar.getAttribute('data-region-role') !== 'toolbar') return false
          const grid = toolbar.closest('.desktop-grid')
          if (!grid) return false
          const toolbarTop = toolbar.getBoundingClientRect().top
          return visibleRegionElements.some(region => (
            region.closest('.desktop-grid') === grid
            && bodyRoles.has(region.getAttribute('data-region-role'))
            && toolbarTop > region.getBoundingClientRect().top + 2
          ))
        })
        const bodyText = String(diagnosticRoot.textContent || '')
        const blockingText = patternSources
          .map(source => new RegExp(source, 'i'))
          .filter(pattern => pattern.test(bodyText))
          .map(pattern => pattern.source)
        const currentItems = [...diagnosticRoot.querySelectorAll('.collection-item-current')]
          .filter(visible)
          .map(element => ({
            ariaCurrent: element.getAttribute('aria-current'),
            weight: getComputedStyle(element.querySelector('.title, .note-card-title') || element).fontWeight,
          }))
        const disabledItems = [...diagnosticRoot.querySelectorAll(
          '.collection-focus-item[disabled], .collection-focus-item[aria-disabled="true"], .collection-focus-item.item-disabled',
        )]
          .filter(visible)
          .map(element => ({
            disabled: ('disabled' in element && Boolean(element.disabled))
              || element.hasAttribute('disabled')
              || element.getAttribute('aria-disabled') === 'true'
              || element.classList.contains('item-disabled'),
            tabIndex: element.tabIndex,
          }))
        return {
          current_scenario: window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario || null,
          viewport_width: innerWidth,
          document_width: document.documentElement.scrollWidth,
          viewport_height: innerHeight,
          document_height: document.documentElement.scrollHeight,
          visible_widget_ids: [...new Set(widgetIds)].slice(0, 160),
          visible_widget_count: widgetIds.length,
          unlabeled_interactives: unlabeled,
          renderer_failures: rendererFailures,
          visible_regions: visibleRegions,
          toolbar_after_body: Boolean(toolbarAfterBody),
          blocking_text: blockingText,
          current_items: currentItems,
          disabled_items: disabledItems,
        }
      }, blockingTextPatterns.map(pattern => pattern.source))
      sample.diagnostics = diagnostics
      sample.runtime_debug = await page.evaluate(() => {
        const events = window.__ADAOS_RUNTIME_DEBUG__?.get?.()
        if (!Array.isArray(events)) return []
        return events
          .filter(event => String(event?.kind || '').startsWith('page_data.'))
          .slice(-160)
          .map(event => ({
            seq: event.seq,
            ts: event.ts,
            level: event.level,
            kind: event.kind,
            details: event.details,
          }))
      })
      if (diagnostics.current_scenario !== scenario) {
        sample.hard_failures.push(`Expected scenario ${scenario}, got ${diagnostics.current_scenario || 'none'}`)
      }
      if (!diagnostics.visible_widget_count) sample.hard_failures.push('No visible WebUI widgets')
      if (diagnostics.document_width > diagnostics.viewport_width + 2) {
        sample.hard_failures.push(
          `Page overflows horizontally: ${diagnostics.document_width}px > ${diagnostics.viewport_width}px`,
        )
      }
      if (layout === 'compact') {
        const underfilled = diagnostics.visible_regions.filter(region => (
          region.compact_presentation === 'stack'
          && region.grid_width
          && region.width < region.grid_width * 0.8
        ))
        if (underfilled.length) {
          sample.hard_failures.push(
            `Compact stack regions do not fill the layout column: ${underfilled.map(region => region.id || region.role).join(', ')}`,
          )
        }
      }
      for (const pattern of diagnostics.blocking_text) {
        sample.hard_failures.push(`Blocking runtime message is visible: ${pattern}`)
      }
      if (diagnostics.unlabeled_interactives.length) {
        sample.warnings.push(`${diagnostics.unlabeled_interactives.length} visible interactive controls have no accessible name`)
      }
      if (diagnostics.current_items.some(item => item.ariaCurrent !== 'step' || Number.parseInt(item.weight, 10) < 600)) {
        sample.hard_failures.push('A current collection item lacks aria-current=step or a visible emphasis')
      }
      if (diagnostics.disabled_items.some(item => !item.disabled || item.tabIndex >= 0)) {
        sample.hard_failures.push('A disabled collection item remains enabled or keyboard-focusable')
      }
      if (diagnostics.current_items.length || diagnostics.disabled_items.length) {
        sample.checks.push({
          kind: 'current-disabled-collection-semantics',
          current: diagnostics.current_items.length,
          disabled: diagnostics.disabled_items.length,
        })
      }
      for (const failure of diagnostics.renderer_failures) {
        sample.hard_failures.push(
          `Widget ${failure.id || failure.type || 'unknown'} is ${failure.state}: ${failure.text || 'no diagnostics'}`,
        )
      }
      if (diagnostics.toolbar_after_body) {
        sample.hard_failures.push('Semantic toolbar is rendered after the primary content region')
      }
      await page.screenshot({ path: path.join(output, `${layout}.png`), fullPage: true })
      console.error(`[builder-browser-feedback] ${layout}:complete`)
    } catch (error) {
      sample.hard_failures.push(String(error?.message || error))
      try {
        await page.screenshot({ path: path.join(output, `${layout}-failure.png`), fullPage: true })
      } catch {}
    } finally {
      await Promise.allSettled(responseDiagnosticTasks)
      await context.close()
    }
  }
} finally {
  await browser.close()
}

function isOptionalLoopbackDiscoveryProbe(failure) {
  if (failure.status !== 0) return false
  const target = new URL(failure.url)
  if (!['127.0.0.1', 'localhost'].includes(target.hostname)) return false
  if (target.pathname === '/api/ping') return true
  return target.pathname === '/api/node/status'
    && target.searchParams.get('profile') === 'probe'
    && ['8777', '8778'].includes(target.port)
}

for (const sample of report.samples) {
  for (const error of sample.page_errors) sample.hard_failures.push(`Page error: ${error}`)
  let expectedRefusedConsoleErrors = sample.request_failures.filter(failure => (
    isOptionalLoopbackDiscoveryProbe(failure)
    && failure.error === 'net::ERR_CONNECTION_REFUSED'
  )).length
  for (const error of sample.console_errors) {
    if (/^Failed to load resource: the server responded with a status of (401|404)/i.test(error)) {
      sample.warnings.push(`Browser bootstrap resource warning: ${error}`)
    } else if (/^Failed to load resource: net::ERR_CONNECTION_REFUSED/i.test(error)
      && expectedRefusedConsoleErrors > 0) {
      expectedRefusedConsoleErrors -= 1
      sample.warnings.push(`Expected local bootstrap probe: ${error}`)
    } else {
      sample.hard_failures.push(`Console error: ${error}`)
    }
  }
  for (const failure of sample.request_failures) {
    const target = new URL(failure.url)
    const expectedDevBootstrapMiss = failure.status === 404 && target.pathname === '/runtime-config.json'
    const expectedAuthProbe = failure.status === 401
      && target.pathname === '/api/node/status'
      && target.searchParams.get('profile') === 'probe'
    const browserCancelledRequest = failure.status === 0 && failure.error === 'net::ERR_ABORTED'
    const optionalLoopbackDiscoveryProbe = isOptionalLoopbackDiscoveryProbe(failure)
    if (expectedDevBootstrapMiss || expectedAuthProbe || browserCancelledRequest || optionalLoopbackDiscoveryProbe) {
      sample.warnings.push(`Expected local bootstrap probe: HTTP ${failure.status} ${failure.method} ${failure.url}`)
    } else {
      sample.hard_failures.push(
        failure.status
          ? `HTTP ${failure.status} ${failure.method} ${failure.url}${formatToolFailureDiagnostic(failure.diagnostic)}`
          : `Request failed ${failure.method} ${failure.url}: ${failure.error || 'unknown error'}`,
      )
    }
  }
}
report.passed = report.samples.length === 2 && report.samples.every(sample => sample.hard_failures.length === 0)
const screenshots = (await fs.readdir(output))
  .filter(name => name.endsWith('.png'))
  .sort()
for (const name of screenshots) {
  const bytes = await fs.readFile(path.join(output, name))
  report[`${name.replace(/\.png$/, '').replace(/-/g, '_')}_sha256`] = `sha256:${crypto.createHash('sha256').update(bytes).digest('hex')}`
}
await fs.writeFile(path.join(output, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8')
console.log(JSON.stringify(report, null, 2))
if (!report.passed) process.exitCode = 1
