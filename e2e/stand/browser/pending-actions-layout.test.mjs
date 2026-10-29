import assert from 'node:assert/strict'
import test from 'node:test'
import { chromium } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import sass from '../../../src/adaos/integrations/adaos-client/node_modules/sass/sass.node.js'

// Compile the product stylesheet, not a diagnostic copy of the relevant rules.
// This isolates layout from live Pending Actions and never submits a decision.
const css = sass.compile(fileURLToPath(new URL('../../../src/adaos/integrations/adaos-client/src/app/app.component.scss', import.meta.url))).css
  .replaceAll(':host', ':root')
const card = (long = false) => `<article class="pending-action-item"><div class="pending-action-item__main">
  <strong>Требуется подтверждение действия</strong><p>${long ? 'Long observation and recovery context. '.repeat(24) : 'Подтвердите действие перед выполнением.'}</p>
  <div class="pending-action-item__meta"><span>Статус: pending</span><span>Сегодня</span><span>Инициатор: ${'scoped-actor-'.repeat(long ? 20 : 1)}</span></div>
  </div><div class="pending-action-item__actions"><button>Подтвердить</button><button>Отклонить</button><button>Позже</button></div></article>`

test('Pending Actions keep intrinsic card/button height and scroll only the bounded list', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    for (const width of [390, 1024, 1714]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } })
      for (const [name, count, long] of [['empty', 0, false], ['single', 1, false], ['many', 8, false], ['long', 1, true]]) {
        await page.setContent(`<style>${css} body{margin:0;font-family:Arial} ion-modal.pending-actions-overlay{display:block;width:min(460px,calc(100vw - 24px));height:620px}</style>
          <ion-modal class="pending-actions-overlay"><section class="pending-actions-panel">
          <header class="pending-actions-panel__header"><strong>Требуемые действия</strong></header>
          <nav class="pending-actions-panel__tabs"><button>Активные</button><button>История</button></nav>
          <div class="pending-actions-list">${card(long).repeat(count) || '<p class="pending-actions-list__empty">Нет активных действий</p>'}</div>
          </section></ion-modal>`)
        const metrics = await page.evaluate(() => {
          const list = document.querySelector('.pending-actions-list')
          return { buttons: [...document.querySelectorAll('.pending-action-item__actions button')].map(el => el.getBoundingClientRect().height),
            cards: [...document.querySelectorAll('.pending-action-item')].map(el => el.getBoundingClientRect().height),
            client: list.clientHeight, scroll: list.scrollHeight, width: innerWidth, documentWidth: document.documentElement.scrollWidth }
        })
        assert.ok(metrics.buttons.every(height => height >= 29 && height <= 42), `${width}/${name}: stretched buttons ${metrics.buttons}`)
        if (name === 'single') assert.ok(metrics.cards[0] < 220, `${width}: one card filled the panel`)
        if (name === 'many') assert.ok(metrics.scroll > metrics.client, `${width}: list must scroll`)
        assert.ok(metrics.documentWidth <= metrics.width, `${width}/${name}: horizontal page overflow`)
      }
      await page.close()
    }
  } finally { await browser.close() }
})
