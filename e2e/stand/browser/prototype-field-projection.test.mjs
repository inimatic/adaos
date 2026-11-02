import test from 'node:test'
import assert from 'node:assert/strict'
import { chromium } from '@playwright/test'
import { assertPrototypeFieldText, prototypeFieldCaption, prototypeFieldCell } from './prototype-navigation.mjs'

test('Field oracle accepts a labelled owner and refuses incidental, wrong or hidden RU', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    // A locator-contract fixture, not proof of the production renderer. The
    // actual renderer is checked separately on the owned retained Prototype.
    for (const [kind, value, hidden, good] of [
      ['ui.list', 'RU', false, true], ['ui.list', 'EU', false, false],
      ['ui.list', 'RU', true, false], ['ui.table', 'RU', false, true],
      ['ui.table', 'EU', false, false],
    ]) {
      const inner = kind === 'ui.list'
        ? `<span class="list-row-meta-item" data-key="server" ${hidden ? 'hidden' : ''}>Сервер: ${value}</span><span class="list-row-meta-item" data-key="other">RU</span>`
        : `<table><tbody><tr><td data-key="server">${value}</td><td data-key="other">RU</td></tr></tbody></table>`
      await page.setContent(`<article id="row"><h3>RU-I1</h3>${inner}</article>`)
      await page.evaluate(() => { window.ng = { getContext: element => ({ $implicit: {
        key: element.dataset.key, label: 'Сервер' } }) } })
      const row = kind === 'ui.list' ? page.locator('#row') : page.locator('tr')
      if (good) await assertPrototypeFieldText(row, { type: kind }, 'server', 'RU')
      else await assert.rejects(() => assertPrototypeFieldText(row, { type: kind }, 'server', 'RU'))
      if (good && kind === 'ui.list') assert.equal(await prototypeFieldCaption(row, { type: kind }, 'server'), 'Сервер')
    }
    await page.close()
  } finally { await browser.close() }
})

test('Field identity waits for context attachment but never accepts duplicate or absent owners', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.setContent('<table><tbody><tr><td data-key="effort">0.5</td><td data-key="other">0.5</td></tr></tbody></table>')
    await page.evaluate(() => {
      window.ng = { getContext: () => undefined }
      setTimeout(() => { window.ng.getContext = element => ({ $implicit: { key: element.dataset.key } }) }, 120)
    })
    await assertPrototypeFieldText(page.locator('tr'), { type: 'ui.table' }, 'effort', '0.5')
    await page.locator('td').last().evaluate(element => { element.dataset.key = 'effort' })
    await assert.rejects(() => prototypeFieldCell(page.locator('tr'), { type: 'ui.table' }, 'effort'))
    await page.locator('td').evaluateAll(elements => elements.forEach(element => { element.dataset.key = 'other' }))
    await assert.rejects(() => prototypeFieldCell(page.locator('tr'), { type: 'ui.table' }, 'effort'))
    await page.close()
  } finally { await browser.close() }
})
