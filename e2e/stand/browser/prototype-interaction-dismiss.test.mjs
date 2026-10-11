import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs/promises'
import { chromium } from 'playwright'

const source = await fs.readFile(new URL('./prototype-interactions.mjs', import.meta.url), 'utf8')
const selectors = [...source.matchAll(/getByRole\('button', \{ name: \/([^/]+)\/, exact: true \}\)\.click\(\)/g)]
  .filter(match => match[1].includes('Close')).map(match => new RegExp(match[1]))

test('all editor dismissal selectors exclude business actions with a Close prefix', () => {
  assert.equal(selectors.length, 4)
  for (const selector of selectors) {
    assert.ok(selector.test('Close') && selector.test('Закрыть'))
    assert.equal(selector.test('Закрыть как решённое'), false)
    assert.equal(selector.test('Close ticket'), false)
  }
})

test('public modal dismissal remains unique beside a resolve-ticket action', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    for (const label of ['Close', 'Закрыть']) {
      await page.setContent(`<ion-modal><button id="dismiss">${label}</button><button id="resolve">Закрыть как решённое</button></ion-modal>`)
      await page.evaluate(() => document.querySelectorAll('button').forEach(button => {
        button.addEventListener('click', () => { document.body.dataset.clicked = button.id })
      }))
      await page.locator('ion-modal').last().getByRole('button', { name: selectors[0], exact: true }).click()
      assert.equal(await page.evaluate(() => document.body.dataset.clicked), 'dismiss')
    }
  } finally { await browser.close() }
})
