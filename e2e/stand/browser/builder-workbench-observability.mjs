import { chromium, expect as baseExpect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { installSystemOverlayDismissal } from './prototype-navigation.mjs'

const expect = baseExpect.configure({ timeout: 30_000 })
const hub = process.env.ADAOS_E2E_HUB_URL, token = process.env.ADAOS_E2E_HUB_TOKEN
const subnet = process.env.ADAOS_E2E_SUBNET_ID, scenario = process.env.ADAOS_E2E_SCENARIO_ID
const output = process.env.ADAOS_E2E_OUTPUT
if (!hub || !token || !subnet || !scenario?.startsWith('test_semantic_') || !output) throw new Error('Owned diagnostic identity required')
const control = JSON.parse(await fs.readFile(path.join(output, 'live-control.json'), 'utf8'))
const report = { ok: false, samples: [], modelCalls: 0 }
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of Object.entries({ wide: { width: 1440, height: 1000 }, compact: { width: 390, height: 844 } })) {
    const context = await browser.newContext({ viewport })
    await context.addInitScript(({ hub, token, subnet }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'e2e-builder-workbench-debug',
        adaos_hub_base: hub, adaos_local_hub_base: hub, adaos_try_local_hub: '1', adaos_hub_token: token,
        adaos_local_subnet_id: subnet, adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
    }, { hub, token, subnet })
    const page = await context.newPage()
    const sample = { layout, checks: [], errors: [] }
    report.samples.push(sample)
    await installSystemOverlayDismissal(page)
    page.on('pageerror', error => sample.errors.push(String(error)))
    try {
      const url = new URL(control.authoring?.url || process.env.ADAOS_E2E_CLIENT_URL)
      for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
        webspace_id: 'desktop', expected_scenario_id: 'builder', try_local_hub: '1' })) url.searchParams.set(key, value)
      await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await page.waitForFunction(() => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === 'builder', { timeout: 60_000 })
      const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).first()
      sample.language = await page.evaluate(() => {
        const app = window.ng?.getComponent(document.querySelector('app-root'))
        const tableElement = document.querySelector('ada-table-widget')
        const table = tableElement ? window.ng?.getComponent(tableElement) : null
        return { effective: app?.i18n?.getLang?.(), settings: app?.personalizationForm?.language,
          document: document.documentElement.lang, storage: localStorage.getItem('adaos_lang'),
          dictionaryResources: (table?.data || app?.i18n?.pageData)?.listResourceDescriptors?.()?.filter(item => item.role === 'i18n')?.map(item => ({ id: item.id, locale: item.locale, owner: item.owner })),
          processTranslation: app?.i18n?.t?.('builder.design.Процесс') }
      })
      await fs.writeFile(path.join(output, `${layout}-language.json`), JSON.stringify(sample.language, null, 2))
      if (sample.language.effective !== 'en') throw new Error('Effective locale differs from English Settings')
      let processControl = page.locator('[data-command-id="process"]').first()
      if (!await processControl.isVisible()) {
        await host('design-workbench-header').locator('[data-command-id="inspect-section"]').click()
        processControl = page.locator('[data-command-option="process"]').last()
      }
      await expect(processControl).toBeVisible()
      await expect(processControl).toContainText('Process')
      sample.checks.push('English settings and Builder captions agree')
      await processControl.click()
      const table = host('design-trace-tasks')
      await expect(table).toBeVisible()
      await expect(table).toContainText(control.runs[0].title)
      await expect(table).toContainText('—')
      const titles = control.runs.map(row => row.title)
      if (new Set(titles).size !== titles.length) throw new Error('Run rows remain indistinguishable')
      await table.locator('tbody tr').first().locator('td .cell-text').first().click()
      await expect(host('design-run-execution')).toBeVisible()
      await expect(host('design-run-context')).toContainText('Workflow request')
      await expect(host('design-run-context')).toContainText('Change existing labels')
      await expect(host('design-run-execution')).toContainText('Not recorded')
      await expect(host('design-run-measurements')).toBeHidden()
      sample.checks.push('distinct run rows', 'execution details', 'recorded workflow request, not invented chat', 'unknown metrics are not zero')
      await host('design-run-context').getByText('Change existing labels', { exact: false }).scrollIntoViewIfNeeded()
      await page.screenshot({ path: path.join(output, `runs-${layout}.png`), fullPage: true })
      await page.keyboard.press('Escape')
      await expect(host('design-run-execution')).toBeHidden()
      const settings = page.locator('[data-command-id="settings"]').first()
      await settings.click()
      const modelSelect = host('chat-side-settings').locator('[data-webui-field-id="llmModel"] select')
      for (const id of ['gpt-5.5', 'gpt-5.6-sol', 'gpt-6-astra']) {
        const model = control.models.options.find(item => item.id === id)
        if (!model) throw new Error(`Root does not advertise required development model ${id}`)
        await expect(modelSelect).toContainText(model.label)
      }
      sample.checks.push('Root-advertised development models')
      await page.keyboard.press('Escape')
      await page.locator('[data-command-id="conversation"]').first().click()
      await expect(host('design-conversation-request-provenance')).toContainText('Change existing labels')
      sample.checks.push('SDK request is visible without a fabricated discussion')
      await page.locator('[data-command-id="result"]').first().click()
      const popupPromise = page.waitForEvent('popup', { timeout: 30_000 })
      await host('design-preview-commands').locator('[data-command-id="open"]').click()
      const popup = await popupPromise
      await expect.poll(() => popup.url(), { timeout: 30_000 }).toContain('desktop-dev')
      sample.previewNavigation = popup.url()
      if (!new URL(popup.url()).searchParams.get('expected_scenario_id')?.includes(scenario)) throw new Error('UI opener navigated to the wrong scenario')
      sample.checks.push('visible Builder Open Preview action succeeds')
      await popup.close()
      const preview = new URL(process.env.ADAOS_E2E_CLIENT_URL)
      for (const [key, value] of Object.entries({ intent: 'webspace.open', zone: 'lo', subnet_id: subnet,
        webspace_id: 'desktop-dev', space_kind: 'development', expected_scenario_id: scenario, try_local_hub: '1' })) preview.searchParams.set(key, value)
      const previewPage = await context.newPage()
      await installSystemOverlayDismissal(previewPage)
      await previewPage.goto(preview.href, { waitUntil: 'domcontentloaded', timeout: 60_000 })
      await previewPage.waitForFunction(expected => window.__ADAOS_DEBUG_STATE__?.()?.sync?.materialization?.currentScenario === expected,
        scenario, { timeout: 60_000 })
      await expect(previewPage.locator('[data-webui-widget-id="records-list"]')).toContainText('First task')
      sample.checks.push('ordinary project preview reaches desktop-dev with actual records')
      await previewPage.screenshot({ path: path.join(output, `preview-${layout}.png`), fullPage: true })
      sample.ok = !sample.errors.length
    } catch (error) {
      sample.ok = false
      sample.error = String(error)
      await fs.writeFile(path.join(output, `${layout}-body.txt`), await page.locator('body').innerText())
      await page.screenshot({ path: path.join(output, `failure-${layout}.png`), fullPage: true })
    }
    await context.close()
  }
  report.ok = report.samples.every(sample => sample.ok)
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'browser-result.json'), JSON.stringify(report, null, 2))
}
console.log(JSON.stringify({ ok: report.ok, samples: report.samples.map(sample => ({ layout: sample.layout, checks: sample.checks, error: sample.error })), modelCalls: 0 }))
if (!report.ok) process.exitCode = 1
