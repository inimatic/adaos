import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { chromium, expect } from '@playwright/test'
import { revealPrototypeWidget } from './prototype-navigation.mjs'

const { ADAOS_E2E_HUB_URL: hub, ADAOS_E2E_HUB_TOKEN: token, ADAOS_E2E_SCENARIO_ID: scenario,
  ADAOS_E2E_TASK_ID: task, ADAOS_E2E_SOURCE_SHA256: source, ADAOS_E2E_OUTPUT: output } = process.env
assert.equal(process.env.ENV_TYPE, 'dev')
assert.match(scenario, /^workbench_test_[a-z0-9_]+$/)
assert.ok(task && source && hub && token && output)
const raw = await fs.readFile(path.join(process.cwd(), `.adaos/dev/sn_6acf0c01/scenarios/${scenario}/webui.json`))
assert.equal(createHash('sha256').update(raw).digest('hex'), source)
function* objects(value) {
  if (Array.isArray(value)) { for (const item of value) yield* objects(item) }
  else if (value && typeof value === 'object') { yield value; for (const item of Object.values(value)) yield* objects(item) }
}
const widgets = [...objects(JSON.parse(raw))].filter(item => item.id && item.type)
const specification = id => widgets.find(item => item.id === id)
const webspace = process.env.ADAOS_E2E_WEBSPACE || 'desktop-dev-dev'
const trial = process.env.ADAOS_E2E_TRIAL === '1'
const report = { scope: 'Independent live Automation browser review, not Prototype or delivery', scenario, task,
  source_sha256: source, samples: [], passed: false }
if (trial) report.scope = 'Independent local Trial desktop/browser review, not external distribution'
const screenshots = output.replace(/\.json$/, '')
await fs.mkdir(screenshots, { recursive: true })
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of [['wide', { width: 1440, height: 1000 }], ['compact', { width: 390, height: 844 }]]) {
    const sample = { layout, viewport, checks: [], errors: [], calls: [], passed: false }
    report.samples.push(sample)
    const context = await browser.newContext({ viewport, locale: 'ru-RU', colorScheme: 'dark' })
    await context.addInitScript(({ hub, token, webspace }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'workbench-automation-review', adaos_lang: 'ru',
        adaos_webspace_id: webspace, adaos_hub_base: hub, adaos_local_hub_base: hub,
        adaos_hub_token: token, adaos_try_local_hub: '1', adaos_local_subnet_id: 'sn_6acf0c01',
        adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
    }, { hub, token, webspace })
    const page = await context.newPage()
    page.setDefaultTimeout(20000)
    page.on('pageerror', error => sample.errors.push(error.message))
    page.on('response', async response => {
      const body = response.request().postData()
      if (body?.includes(`${scenario}_skill`)) {
        const request = JSON.parse(body)
        sample.calls.push({ status: response.status(), tool: request.tool || request.name })
      }
    })
    const widget = id => page.locator(`[data-webui-widget-id="${id}"]`).filter({ visible: true }).first()
    const field = (form, id) => {
      const fields = specification(form)?.inputs?.fields || []
      const matches = fields.filter(item => item.id === id || item.id === `books.${id}`)
      assert.equal(matches.length, 1, `Unambiguous literal field ${form}:${id}`)
      return widget(form).locator(`[data-webui-field-id=${JSON.stringify(matches[0].id)}]`)
    }
    const reveal = id => revealPrototypeWidget(page, widgets, specification(id))
    const waitForApplication = async () => {
      const navigation = widgets.find(item => item.inputs?.variant === 'tabs')
      await widget(navigation?.id || 'books_list').waitFor({ timeout: 60000 })
      await reveal('books_list')
      await widget('books_list').waitFor({ timeout: 60000 })
    }
    const close = async () => {
      const modal = page.locator('ion-modal.show-modal').last()
      await modal.getByRole('button', { name: /^(close|закрыть)$/i }).click()
      await modal.waitFor({ state: 'hidden' })
    }
    const check = name => sample.checks.push({ id: name, status: 'passed' })
    const availability = async name => {
      const chip = page.locator('.availability-chip').first()
      try {
        await expect(chip).toHaveAttribute('data-state', 'ready', { timeout: 30000 })
        await expect(chip).toHaveAttribute('title', /disable-stateful=no/)
        await expect(chip).toHaveAttribute('title', /widget-data=ready/)
        check(`availability:${name}`)
      } finally {
        sample.availability ??= []
        sample.availability.push({ phase: name, state: await chip.getAttribute('data-state'),
          diagnostics: await chip.getAttribute('title') })
      }
    }
    const capture = async name => {
      const geometry = await page.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth }))
      assert.ok(geometry.width <= geometry.viewport + 1, `Document overflow: ${name}`)
      const screenshot = path.join(screenshots, `${layout}-${name}.png`)
      if (!trial) await page.screenshot({ path: screenshot, fullPage: true, animations: 'disabled' })
      sample.checks.push({ id: `visual:${name}`, status: 'passed', geometry,
        screenshot: trial ? null : screenshot, privacy: trial ? 'Installed records are not captured' : 'DEV synthetic data' })
    }
    const mutate = async (tool, action) => {
      const pending = page.waitForResponse(response => response.request().postData()?.includes(`${scenario}_skill.${tool}`) ||
        response.request().postData()?.includes(`${scenario}_skill:${tool}`), { timeout: 60000 })
      void pending.catch(() => {})
      await action()
      const response = await pending
      const body = await response.json()
      assert.ok(response.ok() && body.ok !== false && body.result?.ok !== false, `${tool}: HTTP ${response.status()} or rejected result`)
      if (trial) {
        assert.equal(response.headers()['x-adaos-runtime-source'], 'trial')
        assert.equal(response.headers()['x-adaos-release-digest'], process.env.ADAOS_E2E_RELEASE_DIGEST)
      }
      return body.result || body
    }
    const mutations = () => sample.calls.filter(row => /create_book|update_book|delete_book/.test(row.tool || '')).length
    const marker = `E2E-BROWSER-${layout}-${Date.now()}`
    try {
      if (trial) {
        const switched = await context.request.post(`${hub}/api/node/yjs/webspaces/${webspace}/scenario`, {
          headers: { 'X-AdaOS-Token': token }, data: { scenario_id: 'web_desktop', set_home: false } })
        assert.ok(switched.ok())
      }
      await page.goto(`http://127.0.0.1:8100/?intent=webspace.open&zone=lo&subnet_id=sn_6acf0c01&webspace_id=${webspace}&space_kind=${trial ? 'workspace' : 'development'}&expected_scenario_id=${trial ? 'web_desktop' : scenario}&try_local_hub=1`,
        { waitUntil: 'domcontentloaded', timeout: 60000 })
      if (trial) {
        const tile = page.locator('.tile').filter({ hasText: 'Reading List [TEST]' }).first()
        await tile.waitFor({ timeout: 60000 })
        await expect(tile.locator('.release-review-badge')).toHaveText(/BETA/i)
        await availability('desktop')
        await capture('desktop-beta')
        await tile.click()
        check('production-desktop-beta-launcher')
      }
      await waitForApplication()
      if (trial) await availability('beta-open')
      await capture('initial')
      await widget('open-book_create').getByRole('button').click()
      await field('book_create', 'title').locator('input').waitFor()
      const beforeInvalid = mutations()
      await widget('book_create').getByRole('button', { name: /^создать$/i }).click()
      assert.equal(await widget('book_create').isVisible(), true)
      assert.equal(mutations(), beforeInvalid)
      check('required-title-prevents-submit')
      await field('book_create', 'title').locator('input').fill(marker)
      await close()
      assert.equal(mutations(), beforeInvalid)
      check('create-cancel-does-not-submit')
      await widget('open-book_create').getByRole('button').click()
      assert.equal(await field('book_create', 'title').locator('input').inputValue(), '')
      await field('book_create', 'title').locator('input').fill(marker)
      await field('book_create', 'author').locator('input').fill('Елена Наблюдатель')
      await field('book_create', 'note').locator('textarea').fill('Строка первая\nСтрока вторая')
      await mutate('create_book', () => widget('book_create').getByRole('button', { name: /^создать$/i }).click())
      await widget('books_list').getByText(marker, { exact: true }).waitFor()
      check('create-refreshes-list')
      await widget('books_list').getByText(marker, { exact: true }).click()
      await widget('book_details').getByText(marker, { exact: true }).waitFor()
      await capture('selected')
      const buttons = widget('book_details').getByRole('button')
      await buttons.first().click()
      await expect(field('book_editor', 'title').locator('input')).toHaveValue(marker, { timeout: 30000 })
      await field('book_editor', 'title').locator('input').fill(marker + '-cancel')
      const beforeCancel = mutations()
      await close()
      assert.equal(mutations(), beforeCancel)
      check('edit-cancel-does-not-submit')
      await buttons.first().click()
      await expect(field('book_editor', 'title').locator('input')).toHaveValue(marker, { timeout: 30000 })
      await field('book_editor', 'title').locator('input').fill(marker + '-updated')
      await field('book_editor', 'note').locator('textarea').fill('Изменено\nЕще строка')
      await capture('editor')
      await mutate('update_book', () => widget('book_editor').getByRole('button', { name: /^сохранить изменения$/i }).click())
      await widget('books_list').getByText(marker + '-updated', { exact: true }).waitFor()
      await page.reload({ waitUntil: 'domcontentloaded' })
      await page.locator('[data-webui-widget-id]').first().waitFor({ timeout: 60000 })
      await reveal('books_list')
      await widget('books_list').getByText(marker + '-updated', { exact: true }).waitFor({ timeout: 60000 })
      if (trial) await availability('beta-reload')
      await widget('books_list').getByText(marker + '-updated', { exact: true }).click()
      await widget('book_details').getByText('Изменено', { exact: false }).waitFor()
      check('reload-preserves-edit-and-multiline-note')
      const search = widget('queries-books_list').locator('input').first()
      await search.fill('Наблюдатель')
      await widget('books_list').getByText(marker + '-updated', { exact: true }).waitFor()
      await search.fill('missing-' + Date.now())
      await widget('books_list').getByText('Ваш список чтения пуст', { exact: true }).waitFor()
      await search.fill('')
      await widget('books_list').getByText(marker + '-updated', { exact: true }).click()
      check('search-and-empty-result')
      await widget('book_details').getByRole('button').last().click()
      const beforeDeleteCancel = mutations()
      await widget('book_delete').getByRole('button', { name: /^удалить$/i }).click()
      const alert = page.locator('ion-alert').filter({ visible: true }).last()
      await alert.getByRole('button', { name: /^(cancel|отмена)$/i }).click()
      assert.equal(mutations(), beforeDeleteCancel)
      check('delete-confirmation-cancel-does-not-submit')
      await mutate('delete_book', async () => {
        await widget('book_delete').getByRole('button', { name: /^удалить$/i }).click()
        await page.locator('ion-alert').filter({ visible: true }).last().getByRole('button', { name: /^удалить$/i }).click()
      })
      await widget('books_list').getByText(marker + '-updated', { exact: true }).waitFor({ state: 'hidden' })
      check('delete-refreshes-list')
      await capture('completed')
      await reveal('settings_list')
      await widget('settings_list').locator('tbody tr').filter({ has: page.locator('td') }).first().click()
      const preferred = field('settings_editor', 'preferred_view')
      await preferred.waitFor()
      const radios = preferred.getByRole('radio')
      const radioChoice = await radios.count() > 0
      let nextValue
      if (radioChoice) {
        assert.equal(await radios.count(), 2, 'Both supported presentation modes are selectable')
        nextValue = await radios.first().isChecked() ? 1 : 0
        await radios.nth(nextValue).check()
      } else {
        const select = preferred.locator('select')
        const currentValue = await select.inputValue()
        const alternatives = await select.locator('option').evaluateAll(options => options
          .filter(option => option.value && !option.disabled).map(option => option.value))
        nextValue = alternatives.find(value => value !== currentValue)
        assert.ok(nextValue, 'Both supported presentation modes are selectable')
        await select.selectOption(nextValue)
      }
      await mutate('update_settings', () => widget('settings_editor').getByRole('button', { name: /сохранить|save/i }).click())
      await page.reload({ waitUntil: 'domcontentloaded' })
      await page.locator('[data-webui-widget-id]').first().waitFor({ timeout: 60000 })
      await reveal('settings_list')
      await widget('settings_list').locator('tbody tr').filter({ has: page.locator('td') }).first().click()
      const reopened = field('settings_editor', 'preferred_view')
      if (radioChoice) await expect(reopened.getByRole('radio').nth(nextValue)).toBeChecked()
      else await expect(reopened.locator('select')).toHaveValue(nextValue)
      check('settings-edit-persists-after-reload')
      await capture('settings')
      await close()
      if (trial) {
        const home = () => page.locator('ion-header ion-buttons ion-button')
          .filter({ has: page.locator('ion-icon[name="close-outline"]'), visible: true }).first()
        const tile = page.locator('.tile').filter({ hasText: 'Reading List [TEST]' }).first()
        await home().click()
        await tile.waitFor({ timeout: 60000 })
        await expect(tile.locator('.release-review-badge')).toHaveText(/BETA/i)
        await availability('return-home')
        await tile.click()
        await waitForApplication()
        await availability('beta-reopen')
        check('return-home-and-reopen-beta')
        await home().click()
        await tile.waitFor({ timeout: 60000 })
        await availability('desktop-final')
        await capture('desktop-final')
      }
      assert.deepEqual(sample.errors, [])
      sample.passed = true
    } catch (error) {
      sample.failure = error.message
      await capture('blocker').catch(() => {})
    } finally {
      await context.close()
      console.log(JSON.stringify({ layout, passed: sample.passed, failure: sample.failure }))
    }
  }
  report.passed = report.samples.every(sample => sample.passed)
} finally {
  await browser.close()
  await fs.writeFile(output, JSON.stringify(report, null, 2) + '\n', 'utf8')
}
if (!report.passed) process.exitCode = 1
