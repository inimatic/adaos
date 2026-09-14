import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium, expect } from '@playwright/test'

const { ADAOS_E2E_HUB_URL: hub, ADAOS_E2E_HUB_TOKEN: token, ADAOS_E2E_SCENARIO_ID: scenario,
  ADAOS_E2E_TASK_ID: task, ADAOS_E2E_SOURCE_SHA256: source, ADAOS_E2E_OUTPUT: output } = process.env
assert.equal(process.env.ENV_TYPE, 'dev')
assert.match(scenario, /^workbench_test_[a-z0-9_]+$/)
assert.ok(task && source && hub && token && output)
const report = { scope: 'Independent live Automation browser review, not Prototype or delivery', scenario, task,
  source_sha256: source, samples: [], passed: false }
const screenshots = output.replace(/\.json$/, '')
await fs.mkdir(screenshots, { recursive: true })
const browser = await chromium.launch({ headless: true })
try {
  for (const [layout, viewport] of [['wide', { width: 1440, height: 1000 }], ['compact', { width: 390, height: 844 }]]) {
    const sample = { layout, viewport, checks: [], errors: [], calls: [], passed: false }
    report.samples.push(sample)
    const context = await browser.newContext({ viewport, locale: 'ru-RU', colorScheme: 'dark' })
    await context.addInitScript(({ hub, token }) => {
      window.__ADAOS_DEBUG__ = true
      window.__ADAOS_BASE__ = hub
      window.__ADAOS_TOKEN__ = token
      for (const [key, value] of Object.entries({ adaos_device_id: 'workbench-automation-review', adaos_lang: 'ru',
        adaos_webspace_id: 'desktop-dev-dev', adaos_hub_base: hub, adaos_local_hub_base: hub,
        adaos_hub_token: token, adaos_try_local_hub: '1', adaos_local_subnet_id: 'sn_6acf0c01',
        adaos_selected_zone: 'lo', adaos_last_used_zone: 'lo' })) localStorage.setItem(key, value)
    }, { hub, token })
    const page = await context.newPage()
    page.setDefaultTimeout(20000)
    page.on('pageerror', error => sample.errors.push(error.message))
    page.on('response', async response => {
      const body = response.request().postData()
      if (body?.includes(`${scenario}_skill`)) sample.calls.push({ status: response.status(),
        body: JSON.parse(body), response: await response.json().catch(() => null) })
    })
    const widget = id => page.locator(`[data-webui-widget-id="${id}"]`).filter({ visible: true }).first()
    const field = (form, id) => widget(form).locator(`[data-webui-field-id="${id}"]`)
    const close = async () => {
      const modal = page.locator('ion-modal.show-modal').last()
      await modal.getByRole('button', { name: /^(close|закрыть)$/i }).click()
      await modal.waitFor({ state: 'hidden' })
    }
    const check = name => sample.checks.push({ id: name, status: 'passed' })
    const capture = async name => {
      const geometry = await page.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth }))
      assert.ok(geometry.width <= geometry.viewport + 1, `Document overflow: ${name}`)
      const screenshot = path.join(screenshots, `${layout}-${name}.png`)
      await page.screenshot({ path: screenshot, fullPage: true, animations: 'disabled' })
      sample.checks.push({ id: `visual:${name}`, status: 'passed', geometry, screenshot })
    }
    const mutate = async (tool, action) => {
      const pending = page.waitForResponse(response => response.request().postData()?.includes(`${scenario}_skill.${tool}`) ||
        response.request().postData()?.includes(`${scenario}_skill:${tool}`), { timeout: 60000 })
      void pending.catch(() => {})
      await action()
      const response = await pending
      const body = await response.json()
      assert.ok(response.ok() && body.ok !== false && body.result?.ok !== false, JSON.stringify(body))
      return body.result || body
    }
    const mutations = () => sample.calls.filter(row => /create_book|update_book|delete_book/.test(row.body.tool || row.body.name || '')).length
    const marker = `E2E-BROWSER-${layout}-${Date.now()}`
    try {
      await page.goto(`http://127.0.0.1:8100/?intent=webspace.open&zone=lo&subnet_id=sn_6acf0c01&webspace_id=desktop-dev-dev&space_kind=development&expected_scenario_id=${scenario}&try_local_hub=1`,
        { waitUntil: 'domcontentloaded', timeout: 60000 })
      await widget('books_list').waitFor({ timeout: 60000 })
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
      await widget('books_list').getByText(marker + '-updated', { exact: true }).waitFor({ timeout: 60000 })
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
