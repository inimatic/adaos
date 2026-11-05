// Real Applications UI with simulated Core completion receipts. Every lifecycle
// mutation is intercepted: this test never promotes the user's application.
import {chromium, expect} from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID
const host = process.env.ADAOS_E2E_WEBSPACE_ID
const hub = process.env.ADAOS_E2E_HUB_URL || 'http://127.0.0.1:8777'
const output = process.env.ADAOS_E2E_OUTPUT
const expectedRelease = process.env.ADAOS_E2E_EXPECTED_RELEASE_DIGEST
const fixtures = process.env.ADAOS_E2E_PROGRESS_FIXTURES
  ? JSON.parse(await fs.readFile(process.env.ADAOS_E2E_PROGRESS_FIXTURES, 'utf8')) : null
if (!token || !subnet || !host || !output) throw Error('Explicit node, webspace, token and output are required')
await fs.mkdir(output, {recursive: true})
const browser = await chromium.launch({headless: true,
  ...(process.env.ADAOS_E2E_CHROME_PATH ? {executablePath: process.env.ADAOS_E2E_CHROME_PATH} : {})})
const report = {passed: false, scope: 'real_ui_simulated_core_receipts', actualStableMutations: 0, samples: []}
let activePage
try {
  for (const [layout, viewport] of [['wide', {width: 1440, height: 1000}], ['compact', {width: 390, height: 844}]]) {
    const context = await browser.newContext({viewport, locale: 'ru-RU', serviceWorkers: 'block'})
    await context.addInitScript(({token, subnet, hub, host}) => {
      window.__ADAOS_BASE__ = hub; window.__ADAOS_FIXED_RUNTIME_BASE__ = hub; window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({adaos_hub_base: hub, adaos_local_hub_base: hub,
        adaos_hub_token: token, adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo',
        adaos_last_used_zone: 'lo', adaos_lang: 'ru', adaos_try_local_hub: '1', adaos_webspace_id: host})) {
        localStorage.setItem(key, value)
      }
    }, {token, subnet, hub, host})
    let phase = 'review', baseApplication, operation, eventSent = false
    const writes = [], errors = []
    let observationKind = '', matchingObservation = false, observationReads = 0
    const observedOperationId = 'applifecycle.simulated-lost-response'
    const originalPublicationKey = 'simulated-original-publication-key'
    const page = await context.newPage()
    activePage = page
    page.on('pageerror', error => errors.push(String(error)))
    const envelope = result => ({ok: true, response: {ok: true, status: 'ok', result}})
    function projectedApplication() {
      if (fixtures) return structuredClone(fixtures[phase === 'completed' ? 'completed' : 'submitted'])
      const app = structuredClone(baseApplication)
      app.lifecycle.operation = {operation_id: operation.operation_id, action: 'promote_stable',
        status: phase === 'completed' ? 'succeeded' : 'applying', release_digest: operation.plan.release_digest,
        candidate_id: operation.plan.candidate_id, automatic: true,
        execution_status: phase === 'completed' ? 'completed' : 'running', revision: phase === 'completed' ? 3 : 1}
      if (phase === 'completed') {
        app.lifecycle.runtime.source = 'stable_installation'
        app.lifecycle.runtime.runtime_root_ref = 'workspace'
        app.lifecycle.runtime.admission_kind = 'stable_installation'
        app.lifecycle.installation.stable_release_digest = operation.plan.release_digest
        app.lifecycle.installation.stable_version = app.lifecycle.runtime.version
        app.dashboard.positions = app.dashboard.positions.map(row => row.id !== 'private_beta' ? row : {
          ...row, id: 'private_stable', stage: 'stable', source: 'stable_installation',
          actions: {open: {...row.actions.open, eligible: true, mode: 'open'}}, hidden_on_desktop: false,
        })
        app.dashboard.active_position_id = 'private_stable'
      }
      return app
    }
    await page.route('**/api/admin/root_mcp/call', async route => {
      const body = route.request().postDataJSON()
      if (observationKind === 'position' && body.tool_id === 'applications.get_operation'
        && body.arguments?.operation_id === observedOperationId) {
        observationReads++
        await route.fulfill({status: 200, json: envelope({operation: {operation_id: observedOperationId,
          application_id: matchingObservation ? 'applications' : 'foreign-application', status: 'succeeded'}})})
      } else if (observationKind === 'publication' && body.tool_id === 'applications.development.get_operation'
        && body.arguments?.idempotency_key === originalPublicationKey) {
        expect(body.arguments.application_id).toBe('applications')
        observationReads++
        await route.fulfill({status: 200, json: envelope({operation: {operation_id: 'appdevop.simulated-lost-response',
          application_id: 'applications', action: 'publish_prerelease', status: 'failed',
          idempotency_key: matchingObservation ? originalPublicationKey : 'foreign-request-key'}})})
      } else if (body.tool_id === 'applications.lifecycle.finalize') {
        writes.push(structuredClone(body.arguments))
        const args = body.arguments
        if (args.application_id !== 'applications' || !args.candidate_id || !args.release_digest
          || !args.expected_revision || !args.idempotency_key || !baseApplication) throw Error('Incomplete confirmation identity')
        if (args.release_digest !== baseApplication.lifecycle.runtime.release_digest) throw Error('Wrong release confirmed')
        operation = {operation_id: 'applifecycle.simulated-browser-receipt', application_id: 'applications',
          status: 'applying', kind: 'reconcile', plan: {...args, action: 'promote_stable'}, result: {}}
        phase = 'submitted'
        await route.fulfill({status: 200, json: envelope({operation})})
      } else if (body.tool_id === 'applications.show' && body.arguments?.application_id === 'applications') {
        if (phase === 'review') {
          const response = await route.fetch()
          const data = await response.json(); baseApplication = data.response.result.application
          await route.fulfill({response, json: data})
        } else await route.fulfill({status: 200, json: envelope({application: projectedApplication()})})
      } else if (body.tool_id === 'applications.lifecycle.events' && body.arguments?.application_id === 'applications'
        && phase === 'completed') {
        const events = eventSent ? [] : [{event_id: 'simulated-finalize-completed', sequence: 1}]
        eventSent = true
        await route.fulfill({status: 200, json: envelope({schema: 'adaos.application.lifecycle_event_page.v1',
          events, cursor: 'simulated-completion', observation_cursor: 'simulated-completion',
          invalidates: events.length ? ['applications.catalog', 'applications.detail', 'applications.lifecycle'] : []})})
      } else if (!body.dry_run && !['applications.list', 'applications.show', 'applications.lifecycle.events'].includes(body.tool_id)) {
        throw Error('Unexpected mutation: ' + body.tool_id)
      } else await route.continue()
    })
    const tag = () => page.evaluate(() => {
      for (const el of document.querySelectorAll('*')) {
        if (!el.tagName.startsWith('ADA-')) continue
        const id = window.ng?.getComponent(el)?.widget?.id
        if (id) el.dataset.finalizeWidget = id
      }
    })
    const widget = id => page.locator(`[data-finalize-widget="${id}"]`).first()
    const modal = () => page.locator('ion-modal.show-modal').last()
    const command = async (scope, id) => {
      const inline = scope.locator(`[data-command-id="${id}"]`).first()
      if (await inline.isVisible()) return inline
      // Compact rows expose the same command through the shared overflow menu.
      const label = await scope.locator('ada-command-bar-widget').evaluateAll((bars, id) => {
        for (const bar of bars) {
          const component = window.ng?.getComponent(bar)
          const button = component?.overflowButtons?.find(button => button.id === id)
          if (button) {
            bar.dataset.finalizeOverflowFor = id
            return component.buttonTitle(button)
          }
        }
        return null
      }, id)
      if (!label) throw Error('Unavailable command: ' + id)
      await scope.locator(`[data-finalize-overflow-for="${id}"] .auto-overflow__more`).click()
      return page.getByRole('menuitem', {name: label, exact: true}).last()
    }
    const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
    for (const [key, value] of Object.entries({webspace_id: host, webspace: host,
      scenario_id: 'applications', expected_scenario_id: 'applications', try_local_hub: '1'})) url.searchParams.set(key, value)
    await page.goto(url.href, {waitUntil: 'domcontentloaded', timeout: 90_000})
    await page.waitForFunction(() => [...document.querySelectorAll('ada-list-widget')].some(el =>
      window.ng?.getComponent(el)?.widget?.id === 'applications-list' && el.innerText.includes('Applications')),
      null, {timeout: 120_000})
    await tag()
    const journalClose = page.locator('.component-updates-panel__tools button[aria-label]').last()
    if (await journalClose.isVisible().catch(() => false)) await journalClose.click()
    await widget('applications-list').getByText('Applications', {exact: true}).first().click()
    await page.waitForFunction(() => [...document.querySelectorAll('ada-list-widget')].some(el =>
      window.ng?.getComponent(el)?.widget?.id === 'dashboard-positions' && el.innerText.includes('Beta')),
      null, {timeout: 90_000})
    await tag()
    const metadata = await page.evaluate(() => window.ng?.getComponent(document.querySelector('app-root'))?.currentRuntimePageMetadata?.())
    if (expectedRelease && metadata?.application?.release_digest !== expectedRelease) throw Error('Wrong Candidate is under review')
    const publicationReview = async (id, stage) => {
      await (await command(widget('dashboard-positions'), id)).click()
      await expect(modal()).toContainText(stage)
      await expect(modal()).toContainText(metadata.application.version)
      await modal().locator('[data-command-id="close"]').click()
    }
    const localState = async patch => widget('dashboard-positions').evaluate((el, patch) => {
      const component = window.ng.getComponent(el)
      const state = component.state || component.pageState
      if (patch) state.patch(patch)
      return state.getSnapshot()
    }, patch)
    let outletBottomGap
    if (fixtures) {
      // Simulate a lost response in the real application state. A foreign receipt
      // cannot release the guard; the matching receipt must do so without a write.
      observationKind = 'position'; matchingObservation = false; observationReads = 0
      await localState({positionApplicationId: 'applications', positionMayHaveRun: true,
        positionPlanning: false, positionIntentKey: 'simulated-position-intent',
        positionReview: {operation: {operation_id: observedOperationId, application_id: 'applications', status: 'applying'}}})
      await expect.poll(() => observationReads, {timeout: 15_000}).toBeGreaterThan(0)
      await expect.poll(async () => (await localState()).positionObservation?.operation?.application_id,
        {timeout: 10_000}).toBe('foreign-application')
      expect((await localState()).positionMayHaveRun).toBe(true)
      expect((await localState()).positionReview.operation.status).toBe('applying')
      matchingObservation = true
      await expect.poll(async () => (await localState()).positionMayHaveRun, {timeout: 20_000}).toBe(false)
      expect((await localState()).positionReview.operation.status).toBe('succeeded')
      observationKind = 'publication'; matchingObservation = false; observationReads = 0
      await localState({publicationMayHaveRun: true, publicationIntentKey: originalPublicationKey,
        publicationResult: {}, publicationContext: {application_id: 'applications', action: 'publish_prerelease'}})
      await expect.poll(() => observationReads, {timeout: 15_000}).toBeGreaterThan(0)
      await expect.poll(async () => (await localState()).publicationRead?.operation?.idempotency_key,
        {timeout: 10_000}).toBe('foreign-request-key')
      expect((await localState()).publicationResult.status).not.toBe('failed')
      matchingObservation = true
      await expect.poll(async () => (await localState()).publicationResult.status, {timeout: 20_000}).toBe('failed')
      observationKind = ''
      const publicDigest = app => app.dashboard.positions.find(row => row.id === 'public_stable')?.release_digest
      expect(publicDigest(fixtures.submitted)).toBe(publicDigest(baseApplication))
      expect(publicDigest(fixtures.completed)).toBe(publicDigest(baseApplication))
      for (const id of ['manage-home', 'use-prerelease']) {
        const control = await command(widget('shared-dev-tickets'), id)
        await expect(control).toBeVisible()
        if (await control.getAttribute('role') === 'menuitem') await control.press('Escape')
      }
      const current = widget('dashboard-positions').locator('ion-item.collection-item-current')
      await expect(current).toHaveCount(1)
      expect(await current.evaluate(el => getComputedStyle(el).boxShadow)).not.toBe('none')
      // Exercise the exact pending state that previously reserved a blank footer,
      // even when this particular node has no pending item at review time.
      const hadPendingClass = await page.locator('ion-app').evaluate(el => {
        const had = el.classList.contains('has-pending-actions')
        el.classList.add('has-pending-actions')
        return had
      })
      outletBottomGap = await page.locator('ion-router-outlet').first().evaluate(el => window.innerHeight - el.getBoundingClientRect().bottom)
      expect(Math.abs(outletBottomGap)).toBeLessThanOrEqual(2)
      if (!hadPendingClass) await page.locator('ion-app').evaluate(el => el.classList.remove('has-pending-actions'))
      await publicationReview('publish_beta', 'Beta')
      expect(writes).toHaveLength(0)
    }
    const open = async () => (await command(widget('dashboard-positions'), 'finalize')).click()
    await open()
    await expect(modal().locator('[data-command-id="confirm-finalize"]')).toBeVisible()
    expect(writes).toHaveLength(0)
    const reviewText = await modal().innerText()
    expect(reviewText).toContain('Эта Beta станет Stable')
    expect(reviewText).not.toContain('promote_stable:')
    expect(reviewText).not.toContain('desktop-codex-')
    expect(reviewText).not.toContain('FIELD 1')
    await page.screenshot({path: path.join(output, `review-${layout}.png`), fullPage: true})
    await modal().locator('[data-command-id="close"]').click()
    await open()
    expect(writes).toHaveLength(0)
    await modal().locator('[data-command-id="confirm-finalize"]').click()
    await expect.poll(() => writes.length, {timeout: 20_000}).toBe(1)
    await expect(modal().locator('[data-command-id="confirm-finalize"]')).toBeHidden()
    if (fixtures) {
      await expect(modal().locator('progress[value="1"][max="3"]')).toBeVisible({timeout: 20_000})
      await expect(modal()).toContainText('Применяем настройки')
    }
    await modal().locator('[data-command-id="close"]').click()
    if (fixtures) {
      await tag()
      await expect(widget('finalize-dashboard-progress').locator('progress[value="1"]')).toBeVisible()
      await expect(widget('dashboard-positions').locator('[data-command-id="remove_beta"]')).toBeHidden()
      await expect(widget('dashboard-positions').locator('[data-command-id="restore_stable"]')).toBeHidden()
      await widget('finalize-dashboard-progress').scrollIntoViewIfNeeded()
      await expect(widget('finalize-dashboard-progress')).toBeInViewport()
      await page.screenshot({path: path.join(output, `progress-${layout}.png`), fullPage: true})
    }
    phase = 'completed'
    // The ordinary page observer receives a simulated event, invalidates the
    // shared dashboard datasource and renders its current Core projection.
    await expect.poll(() => eventSent, {timeout: 40_000}).toBe(true)
    await expect(widget('dashboard-positions')).not.toContainText(/\bBeta\b/, {timeout: 30_000})
    await expect(widget('dashboard-positions')).toContainText('Stable')
    expect(writes).toHaveLength(1)
    expect(errors).toEqual([])
    if (fixtures) {
      await expect(widget('dashboard-positions').locator('[data-command-id="position-open"]').first()).toBeEnabled()
      await expect(widget('dashboard-positions').locator('[data-command-id="publish_beta"]')).toBeHidden()
      await publicationReview('publish_initial_stable', 'Stable')
    }
    await page.screenshot({path: path.join(output, `completed-${layout}.png`), fullPage: true})
    report.samples.push({layout, metadata, reviewText, confirmation: writes[0],
      checks: {readOnlyReview: true, closeAndReopenBeforeConfirmation: true,
        oneConfirmation: true, completionAfterClosingWindow: true, betaPositionRemoved: true,
        ...(fixtures ? {journaledProgress: true, competingActionsUnavailable: true, stageCorrectPublication: true,
          lostResponseObserved: true, foreignReceiptsRejected: true, terminalGuardsReleased: true,
          currentReleaseHighlighted: true, openEnabledAfterCompletion: true, outletBottomGap} : {})}, errors})
    console.log(JSON.stringify({layout, passed: true, confirmedWrites: writes.length}))
    await context.close()
  }
  report.passed = true
} catch (error) {
  report.error = String(error)
  if (activePage && !activePage.isClosed()) {
    report.failure = {url: activePage.url(), text: (await activePage.locator('body').innerText().catch(() => '')).slice(0, 3000)}
    await activePage.screenshot({path: path.join(output, 'failure.png'), fullPage: true}).catch(() => {})
  }
  throw error
} finally {
  await fs.writeFile(path.join(output, 'confirmation-review.json'), JSON.stringify(report, null, 2))
  await browser.close()
}
